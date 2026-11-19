import base64
import hashlib
import json
import struct
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction as db_transaction
from django.db.models import F
from django.utils import timezone
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.compute_budget import ID as COMPUTE_BUDGET_PROGRAM_ID
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.transaction_status import TransactionConfirmationStatus

from accounts.models import Platform
from payments.models import Purchase
from tickets.models import ScanLog, Ticket
from events.models import Event, TicketType
from .models import ChainTransaction


SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")
METAPLEX_CORE_PROGRAM_ID = Pubkey.from_string("CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d")


def _anchor_discriminator(instruction_name):
    digest = hashlib.sha256(f"global:{instruction_name}".encode("utf-8")).digest()
    return digest[:8]


def _borsh_string(value):
    encoded = value.encode("utf-8")
    return struct.pack("<I", len(encoded)) + encoded


def _program_id():
    return Pubkey.from_string(settings.SOLANA_PROGRAM_ID)


def _event_pda(event_id):
    address, _ = Pubkey.find_program_address(
        [b"event", struct.pack("<Q", event_id)],
        _program_id(),
    )
    return address


def _platform_pda():
    address, _ = Pubkey.find_program_address([b"platform"], _program_id())
    return address


def _ticket_type_pda(event_id, ticket_type_id):
    address, _ = Pubkey.find_program_address(
        [
            b"ticket_type",
            struct.pack("<Q", event_id),
            struct.pack("<Q", ticket_type_id),
        ],
        _program_id(),
    )
    return address


def _ticket_pda(asset_address):
    address, _ = Pubkey.find_program_address(
        [b"ticket", bytes(Pubkey.from_string(asset_address))],
        _program_id(),
    )
    return address


def _read_on_chain_ticket_state(ticket_pda, event_id, asset_address):
    ticket_state = Pubkey.from_string(ticket_pda)
    if ticket_state != _ticket_pda(asset_address):
        raise ValueError("The ticket account address does not match its asset.")

    account_info = _rpc_client().get_account_info(
        ticket_state,
        commitment=Confirmed,
    ).value
    if account_info is None or account_info.owner != _program_id():
        raise ValueError("The ticket account is missing or has an invalid owner.")

    account_data = bytes(account_info.data)
    discriminator = hashlib.sha256(b"account:TicketRecord").digest()[:8]
    if len(account_data) < 74 or account_data[:8] != discriminator:
        raise ValueError("The ticket account data is invalid.")

    return {
        "id": struct.unpack_from("<Q", account_data, 8)[0],
        "ticket_type_id": struct.unpack_from("<Q", account_data, 16)[0],
        "event_id": struct.unpack_from("<Q", account_data, 24)[0],
        "is_used": account_data[32] != 0,
        "price_paid": struct.unpack_from("<Q", account_data, 33)[0],
        "owner": str(Pubkey.from_bytes(account_data[41:73])),
    }


def _read_on_chain_ticket_type_state(ticket_type_pda, event_id, ticket_type_id):
    ticket_type_state = Pubkey.from_string(ticket_type_pda)
    if ticket_type_state != _ticket_type_pda(event_id, ticket_type_id):
        raise ValueError("The ticket type account address does not match its Solana ID.")

    account_info = _rpc_client().get_account_info(
        ticket_type_state,
        commitment=Confirmed,
    ).value
    if account_info is None or account_info.owner != _program_id():
        raise ValueError("The ticket type account is missing or has an invalid owner.")

    account_data = bytes(account_info.data)
    discriminator = hashlib.sha256(b"account:TicketTypeState").digest()[:8]
    if len(account_data) < 41 or account_data[:8] != discriminator:
        raise ValueError("The ticket type account data is invalid.")
    if (
        struct.unpack_from("<Q", account_data, 8)[0] != ticket_type_id
        or struct.unpack_from("<Q", account_data, 16)[0] != event_id
    ):
        raise ValueError("The ticket type account does not belong to this event.")

    return {
        "price_in_lamports": struct.unpack_from("<Q", account_data, 24)[0],
        "minted": struct.unpack_from("<I", account_data, 36)[0],
    }


def _rpc_client():
    return Client(settings.SOLANA_RPC_URL)


def _backend_authority_keypair():
    keypair_path = getattr(settings, "SOLANA_BACKEND_KEYPAIR_PATH", "")
    if not keypair_path:
        raise ImproperlyConfigured(
            "SOLANA_BACKEND_KEYPAIR_PATH must point to the backend authority keypair file."
        )
    try:
        with Path(keypair_path).expanduser().open(encoding="utf-8") as keypair_file:
            secret_key = json.load(keypair_file)
        keypair = Keypair.from_bytes(bytes(secret_key))
    except (OSError, ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "Could not load the Solana backend authority keypair."
        ) from exc
    return keypair


def _unsigned_transaction(instruction, payer):
    payer_key = Pubkey.from_string(payer)
    blockhash_response = _rpc_client().get_latest_blockhash(commitment=Confirmed)
    blockhash = blockhash_response.value.blockhash
    last_valid_block_height = blockhash_response.value.last_valid_block_height
    message = Message.new_with_blockhash([instruction], payer_key, blockhash)
    transaction = Transaction.new_unsigned(message)
    return transaction, last_valid_block_height


def _compute_budget_instruction_is_valid(instruction, message):
    program_id = message.account_keys[instruction.program_id_index]
    if program_id != COMPUTE_BUDGET_PROGRAM_ID or instruction.accounts:
        return False

    data = bytes(instruction.data)
    if not data:
        return False
    expected_lengths = {1: 5, 2: 5, 3: 9, 4: 5}
    return expected_lengths.get(data[0]) == len(data)


def _message_matches_prepared(expected_message_bytes, actual_message):
    expected_message = Message.from_bytes(expected_message_bytes)
    if (
        expected_message.recent_blockhash != actual_message.recent_blockhash
        or expected_message.account_keys[0] != actual_message.account_keys[0]
        or expected_message.signer_keys() != actual_message.signer_keys()
    ):
        return False

    expected_keys = set(expected_message.account_keys)
    actual_keys = set(actual_message.account_keys)
    expected_instructions = expected_message.instructions
    actual_instructions = []
    seen_compute_budget_variants = set()

    for instruction in actual_message.instructions:
        program_id = actual_message.account_keys[instruction.program_id_index]
        if program_id == COMPUTE_BUDGET_PROGRAM_ID:
            if not _compute_budget_instruction_is_valid(instruction, actual_message):
                return False
            variant = bytes(instruction.data)[0]
            if variant in seen_compute_budget_variants:
                return False
            seen_compute_budget_variants.add(variant)
            continue
        actual_instructions.append(instruction)

    if len(actual_instructions) != len(expected_instructions):
        return False
    if actual_keys != expected_keys | (
        {COMPUTE_BUDGET_PROGRAM_ID} if seen_compute_budget_variants else set()
    ):
        return False

    for expected_index, actual_instruction in zip(expected_instructions, actual_instructions):
        expected_program_id = expected_message.account_keys[expected_index.program_id_index]
        actual_program_id = actual_message.account_keys[actual_instruction.program_id_index]
        if (
            expected_program_id != actual_program_id
            or bytes(expected_index.data) != bytes(actual_instruction.data)
            or len(expected_index.accounts) != len(actual_instruction.accounts)
        ):
            return False

        for expected_account_index, actual_account_index in zip(
            expected_index.accounts,
            actual_instruction.accounts,
        ):
            expected_account = expected_message.account_keys[expected_account_index]
            actual_account = actual_message.account_keys[actual_account_index]
            if (
                expected_account != actual_account
                or expected_message.is_signer(expected_account_index)
                != actual_message.is_signer(actual_account_index)
                or expected_message.is_writable(expected_account_index)
                != actual_message.is_writable(actual_account_index)
            ):
                return False

    return all(
        expected_message.is_signer(expected_message.account_keys.index(pubkey))
        == actual_message.is_signer(actual_message.account_keys.index(pubkey))
        and expected_message.is_writable(expected_message.account_keys.index(pubkey))
        == actual_message.is_writable(actual_message.account_keys.index(pubkey))
        for pubkey in expected_keys
    )


def submit_consume_ticket_transaction(ticket, scanned_by_wallet):
    platform = Platform.objects.filter(pk=1, is_initialized=True).first()
    if platform is None:
        raise ValueError("The Solana platform has not been initialized.")

    authority = _backend_authority_keypair()
    if str(authority.pubkey()) != platform.backend_authority_wallet:
        raise ImproperlyConfigured(
            "The configured backend keypair does not match the initialized backend authority."
        )
    if ticket.event.on_chain_event_id is None or not ticket.event.event_pda:
        raise ValueError("The event does not have a confirmed Solana account.")
    if not ticket.asset_address or not ticket.ticket_record_pda:
        raise ValueError("The ticket does not have confirmed Solana asset and record addresses.")

    event_id = ticket.event.on_chain_event_id
    if event_id <= 0:
        raise ValueError("The on-chain event ID must be greater than zero.")
    event_state = Pubkey.from_string(ticket.event.event_pda)
    if str(event_state) != str(_event_pda(event_id)):
        raise ValueError("The event account address does not match its Solana event ID.")
    asset = Pubkey.from_string(ticket.asset_address)
    ticket_state = _ticket_pda(ticket.asset_address)
    if str(ticket_state) != ticket.ticket_record_pda:
        raise ValueError("The ticket record address does not match its Solana asset.")

    instruction = Instruction(
        _program_id(),
        _anchor_discriminator("consume_ticket") + struct.pack("<Q", event_id),
        [
            AccountMeta(authority.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(_platform_pda(), is_signer=False, is_writable=False),
            AccountMeta(event_state, is_signer=False, is_writable=True),
            AccountMeta(ticket_state, is_signer=False, is_writable=True),
            AccountMeta(asset, is_signer=False, is_writable=False),
        ],
    )
    rpc_client = _rpc_client()
    blockhash_response = rpc_client.get_latest_blockhash(commitment=Confirmed).value
    message = Message.new_with_blockhash(
        [instruction],
        authority.pubkey(),
        blockhash_response.blockhash,
    )
    signed_transaction = Transaction.new_unsigned(message)
    signed_transaction.sign([authority], blockhash_response.blockhash)
    signature = str(signed_transaction.signatures[0])

    record = ChainTransaction.objects.create(
        instruction=ChainTransaction.Instruction.CONSUME_TICKET,
        status=ChainTransaction.Status.SUBMITTED,
        signature=signature,
        event_id_on_chain=event_id,
        asset_address=ticket.asset_address,
        idempotency_key=str(uuid.uuid4()),
        accounts={
            "event_pk": ticket.event_id,
            "ticket_pk": ticket.pk,
            "ticket_pda": str(ticket_state),
            "asset_address": str(asset),
            "scanned_by_wallet": scanned_by_wallet,
            "signed_transaction": base64.b64encode(
                bytes(signed_transaction)
            ).decode("ascii"),
            "last_valid_block_height": blockhash_response.last_valid_block_height,
        },
    )
    rpc_client.send_raw_transaction(
        bytes(signed_transaction),
        opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
    )
    return record


def rebroadcast_consume_ticket_transaction(record):
    client = _rpc_client()
    block_height = client.get_block_height(commitment=Confirmed).value
    if block_height > record.accounts["last_valid_block_height"]:
        record.status = ChainTransaction.Status.FAILED
        record.error_message = "The transaction blockhash expired before confirmation."
        record.save(update_fields=("status", "error_message", "updated_at"))
        ScanLog.objects.get_or_create(
            ticket_id=record.accounts["ticket_pk"],
            transaction_signature=record.signature,
            defaults={
                "scanned_by_wallet": record.accounts["scanned_by_wallet"],
                "result": ScanLog.Result.REJECTED,
                "notes": record.error_message,
            },
        )
        return None

    signed_transaction = base64.b64decode(
        record.accounts["signed_transaction"],
        validate=True,
    )
    return client.send_raw_transaction(
        signed_transaction,
        opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
    )


def _record_prepared_transaction(instruction, event, payer, transaction, accounts, event_id):
    expected_message = base64.b64encode(bytes(transaction.message)).decode("ascii")
    record = ChainTransaction.objects.create(
        instruction=instruction,
        event_id_on_chain=event_id,
        idempotency_key=str(uuid.uuid4()),
        accounts={
            **accounts,
            "event_pk": event.pk,
            "organizer_wallet": payer,
            "expected_message": expected_message,
        },
    )
    return record


def prepare_initialize_platform_transaction(platform, admin_wallet, backend_authority_wallet):
    admin = Pubkey.from_string(admin_wallet)
    backend_authority = Pubkey.from_string(backend_authority_wallet)
    platform_state = _platform_pda()
    instruction_data = (
        _anchor_discriminator("initialize_platform")
        + bytes(backend_authority)
    )
    instruction = Instruction(
        _program_id(),
        instruction_data,
        [
            AccountMeta(admin, is_signer=True, is_writable=True),
            AccountMeta(platform_state, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    transaction, last_valid_block_height = _unsigned_transaction(instruction, admin_wallet)
    expected_message = base64.b64encode(bytes(transaction.message)).decode("ascii")
    record = ChainTransaction.objects.create(
        instruction=ChainTransaction.Instruction.INITIALIZE_PLATFORM,
        idempotency_key=str(uuid.uuid4()),
        accounts={
            "platform_pk": platform.pk,
            "admin_wallet": str(admin),
            "backend_authority_wallet": str(backend_authority),
            "platform_pda": str(platform_state),
            "expected_message": expected_message,
        },
    )
    return {
        "id": record.pk,
        "unsigned_transaction": base64.b64encode(bytes(transaction)).decode("ascii"),
        "last_valid_block_height": last_valid_block_height,
        "platform_pda": str(platform_state),
    }


def prepare_create_event_transaction(event, organizer_wallet, collection_address):
    event_id = event.pk
    payer = Pubkey.from_string(organizer_wallet)
    collection = Pubkey.from_string(collection_address)
    event_state = _event_pda(event_id)
    event_date = int(event.event_date.timestamp())
    name = event.title
    uri = event.metadata_uri

    instruction_data = (
        _anchor_discriminator("create_event")
        + struct.pack("<Qq", event_id, event_date)
        + _borsh_string(name)
        + _borsh_string(uri)
    )
    instruction = Instruction(
        _program_id(),
        instruction_data,
        [
            AccountMeta(payer, is_signer=True, is_writable=True),
            AccountMeta(collection, is_signer=True, is_writable=True),
            AccountMeta(event_state, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(METAPLEX_CORE_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    transaction, last_valid_block_height = _unsigned_transaction(instruction, organizer_wallet)
    record = _record_prepared_transaction(
        ChainTransaction.Instruction.CREATE_EVENT,
        event,
        organizer_wallet,
        transaction,
        {
            "collection_address": str(collection),
            "event_pda": str(event_state),
        },
        event_id,
    )
    return {
        "id": record.pk,
        "unsigned_transaction": base64.b64encode(bytes(transaction)).decode("ascii"),
        "last_valid_block_height": last_valid_block_height,
        "event_pda": str(event_state),
        "collection_address": str(collection),
    }


def prepare_add_ticket_type_transaction(event, ticket_type, organizer_wallet):
    if event.on_chain_event_id is None or not event.event_pda:
        raise ValueError("The event has not been confirmed on Solana.")

    event_id = event.on_chain_event_id
    ticket_type_id = ticket_type.pk
    payer = Pubkey.from_string(organizer_wallet)
    event_state = Pubkey.from_string(event.event_pda)
    ticket_type_state = _ticket_type_pda(event_id, ticket_type_id)
    instruction_data = (
        _anchor_discriminator("add_ticket_type")
        + struct.pack(
            "<QQQI",
            event_id,
            ticket_type_id,
            ticket_type.price_in_lamports,
            ticket_type.max_supply,
        )
    )
    instruction = Instruction(
        _program_id(),
        instruction_data,
        [
            AccountMeta(payer, is_signer=True, is_writable=True),
            AccountMeta(event_state, is_signer=False, is_writable=False),
            AccountMeta(ticket_type_state, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    transaction, last_valid_block_height = _unsigned_transaction(instruction, organizer_wallet)
    record = _record_prepared_transaction(
        ChainTransaction.Instruction.ADD_TICKET_TYPE,
        event,
        organizer_wallet,
        transaction,
        {
            "ticket_type_pk": ticket_type.pk,
            "ticket_type_id": ticket_type_id,
            "ticket_type_pda": str(ticket_type_state),
        },
        event_id,
    )
    return {
        "id": record.pk,
        "unsigned_transaction": base64.b64encode(bytes(transaction)).decode("ascii"),
        "last_valid_block_height": last_valid_block_height,
        "ticket_type_pda": str(ticket_type_state),
    }


def prepare_withdraw_funds_transaction(event, organizer_wallet):
    if event.on_chain_event_id is None or not event.event_pda:
        raise ValueError("The event does not have a confirmed Solana account.")
    if event.on_chain_event_id <= 0:
        raise ValueError("The on-chain event ID must be greater than zero.")
    if not event.organizer.wallet_id or event.organizer.wallet.wallet_address != organizer_wallet:
        raise ValueError("The supplied wallet is not the event organizer's wallet.")
    if event.organizer.wallet.user_id != event.organizer.user_id:
        raise ValueError("The event organizer wallet is not linked to the organizer account.")
    if timezone.now() <= event.event_date:
        raise ValueError("Funds can only be withdrawn after the event has ended.")

    on_chain_state = _read_on_chain_event_state(event.on_chain_event_id, event.event_pda)
    if on_chain_state["is_canceled"]:
        raise ValueError("Funds cannot be withdrawn from a canceled event.")
    if on_chain_state["is_frozen"]:
        raise ValueError("The event is frozen and must be resolved before withdrawal.")
    if not on_chain_state["is_completed"]:
        raise ValueError("The event must be finalized as completed before withdrawal.")

    payer = Pubkey.from_string(organizer_wallet)
    event_state = Pubkey.from_string(event.event_pda)
    instruction = Instruction(
        _program_id(),
        _anchor_discriminator("withdraw_funds") + struct.pack("<Q", event.on_chain_event_id),
        [
            AccountMeta(payer, is_signer=True, is_writable=True),
            AccountMeta(event_state, is_signer=False, is_writable=True),
        ],
    )
    transaction, last_valid_block_height = _unsigned_transaction(
        instruction,
        organizer_wallet,
    )

    with db_transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        existing = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.WITHDRAW_FUNDS,
            event_id_on_chain=locked_event.on_chain_event_id,
            status__in=(
                ChainTransaction.Status.SUBMITTED,
                ChainTransaction.Status.CONFIRMED,
            ),
        ).first()
        if existing is not None:
            if existing.status == ChainTransaction.Status.CONFIRMED:
                raise ValueError("Funds have already been withdrawn for this event.")
            raise ValueError(
                f"A withdrawal transaction already exists ({existing.status}). "
                "Check its status before trying again."
            )
        ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.WITHDRAW_FUNDS,
            event_id_on_chain=locked_event.on_chain_event_id,
            status=ChainTransaction.Status.CREATED,
        ).update(
            status=ChainTransaction.Status.FAILED,
            error_message="Superseded by a newer withdrawal transaction.",
            updated_at=timezone.now(),
        )

        record = _record_prepared_transaction(
            ChainTransaction.Instruction.WITHDRAW_FUNDS,
            locked_event,
            organizer_wallet,
            transaction,
            {
                "event_pda": str(event_state),
                "last_valid_block_height": last_valid_block_height,
            },
            locked_event.on_chain_event_id,
        )

    return {
        "id": record.pk,
        "unsigned_transaction": base64.b64encode(bytes(transaction)).decode("ascii"),
        "last_valid_block_height": last_valid_block_height,
        "event_pda": str(event_state),
    }


def prepare_cancel_event_transaction(event, organizer_wallet):
    if event.on_chain_event_id is None or not event.event_pda:
        raise ValueError("The event does not have a confirmed Solana account.")
    if event.on_chain_event_id <= 0:
        raise ValueError("The on-chain event ID must be greater than zero.")
    if not event.organizer.wallet_id or event.organizer.wallet.wallet_address != organizer_wallet:
        raise ValueError("The supplied wallet is not the event organizer's wallet.")
    if event.organizer.wallet.user_id != event.organizer.user_id:
        raise ValueError("The event organizer wallet is not linked to the organizer account.")

    on_chain_state = _read_on_chain_event_state(event.on_chain_event_id, event.event_pda)
    if on_chain_state["is_frozen"]:
        raise ValueError("The event is frozen and must be resolved by platform staff.")
    if on_chain_state["is_canceled"]:
        raise ValueError("The event has already been canceled.")
    if on_chain_state["is_completed"]:
        raise ValueError("A completed event cannot be canceled.")
    if not on_chain_state["is_active"]:
        raise ValueError("Only an active event can be canceled.")

    event_id = event.on_chain_event_id
    payer = Pubkey.from_string(organizer_wallet)
    event_state = Pubkey.from_string(event.event_pda)
    instruction = Instruction(
        _program_id(),
        _anchor_discriminator("cencel_event") + struct.pack("<Q", event_id),
        [
            AccountMeta(payer, is_signer=True, is_writable=True),
            AccountMeta(event_state, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    transaction, last_valid_block_height = _unsigned_transaction(
        instruction,
        organizer_wallet,
    )

    with db_transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        existing = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.CANCEL_EVENT,
            event_id_on_chain=locked_event.on_chain_event_id,
            status=ChainTransaction.Status.SUBMITTED,
        ).first()
        if existing is not None:
            raise ValueError("A cancellation transaction is already submitted for this event.")
        ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.CANCEL_EVENT,
            event_id_on_chain=locked_event.on_chain_event_id,
            status=ChainTransaction.Status.CREATED,
        ).update(
            status=ChainTransaction.Status.FAILED,
            error_message="Superseded by a newer cancellation transaction.",
            updated_at=timezone.now(),
        )
        record = _record_prepared_transaction(
            ChainTransaction.Instruction.CANCEL_EVENT,
            locked_event,
            organizer_wallet,
            transaction,
            {
                "event_pda": str(event_state),
                "last_valid_block_height": last_valid_block_height,
            },
            event_id,
        )

    return {
        "id": record.pk,
        "unsigned_transaction": base64.b64encode(bytes(transaction)).decode("ascii"),
        "last_valid_block_height": last_valid_block_height,
        "event_pda": str(event_state),
    }


def prepare_refund_ticket_transaction(ticket, owner_wallet):
    with db_transaction.atomic():
        ticket = (
            Ticket.objects.select_for_update()
            .select_related("event", "ticket_type", "event__organizer")
            .get(pk=ticket.pk)
        )
        if ticket.owner_wallet != owner_wallet:
            raise ValueError("The supplied wallet does not own this ticket.")
        if ticket.status != Ticket.Status.ACTIVE:
            raise ValueError("Only an active ticket can be refunded.")
        if ticket.price_paid_lamports is None or not ticket.asset_address:
            raise ValueError("The ticket is missing its confirmed purchase details.")
        if (
            not ticket.ticket_record_pda
            or ticket.event.on_chain_event_id is None
            or not ticket.event.event_pda
        ):
            raise ValueError("The ticket does not have confirmed Solana account addresses.")
        if not ticket.ticket_type.on_chain_ticket_type_id:
            raise ValueError("The ticket type does not have a confirmed Solana account.")
        if not ticket.event.collection_address:
            raise ValueError("The event collection address is missing.")
        purchase = Purchase.objects.select_for_update().filter(ticket=ticket).first()
        if purchase is None or purchase.status != Purchase.Status.COMPLETED:
            raise ValueError("The ticket purchase is not confirmed.")

        event_id = ticket.event.on_chain_event_id
        if event_id <= 0:
            raise ValueError("The on-chain event ID must be greater than zero.")
        event_state = Pubkey.from_string(ticket.event.event_pda)
        if event_state != _event_pda(event_id):
            raise ValueError("The event account address does not match its Solana event ID.")

        event_data = _read_on_chain_event_state(
            event_id,
            str(event_state),
            include_event_date=True,
        )
        if event_data["is_frozen"]:
            raise ValueError("The event is frozen and must be resolved before ticket refunds.")
        seconds_until_event = event_data["event_date"] - int(timezone.now().timestamp())
        if not event_data["is_canceled"] and seconds_until_event <= 48 * 60 * 60:
            raise ValueError("Tickets can only be refunded more than 48 hours before the event.")

        ticket_state = Pubkey.from_string(ticket.ticket_record_pda)
        ticket_data = _read_on_chain_ticket_state(
            str(ticket_state),
            event_id,
            ticket.asset_address,
        )
        if ticket_data["event_id"] != event_id:
            raise ValueError("The ticket belongs to a different event.")
        if ticket_data["owner"] != owner_wallet:
            raise ValueError("The connected wallet does not own this on-chain ticket.")
        if ticket_data["is_used"]:
            raise ValueError("A used ticket cannot be refunded.")
        if ticket_data["price_paid"] != ticket.price_paid_lamports:
            raise ValueError("The ticket price does not match its on-chain purchase.")
        ticket_type_id = ticket_data["ticket_type_id"]
        if ticket.ticket_type.on_chain_ticket_type_id != ticket_type_id:
            raise ValueError("The ticket type does not match its on-chain ticket.")
        ticket_type_state = _ticket_type_pda(event_id, ticket_type_id)
        ticket_type_data = _read_on_chain_ticket_type_state(
            str(ticket_type_state),
            event_id,
            ticket_type_id,
        )
        if ticket_type_data["minted"] <= 0:
            raise ValueError("The on-chain ticket type has no minted tickets to refund.")

        asset = Pubkey.from_string(ticket.asset_address)
        payer = Pubkey.from_string(owner_wallet)
        collection = Pubkey.from_string(ticket.event.collection_address)
        instruction = Instruction(
            _program_id(),
            _anchor_discriminator("refund_ticket") + struct.pack("<Q", event_id),
            [
                AccountMeta(payer, is_signer=True, is_writable=True),
                AccountMeta(event_state, is_signer=False, is_writable=True),
                AccountMeta(ticket_state, is_signer=False, is_writable=True),
                AccountMeta(ticket_type_state, is_signer=False, is_writable=True),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(asset, is_signer=False, is_writable=True),
                AccountMeta(collection, is_signer=False, is_writable=True),
                AccountMeta(METAPLEX_CORE_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )
        transaction, last_valid_block_height = _unsigned_transaction(
            instruction,
            owner_wallet,
        )

        existing = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
            accounts__ticket_pk=ticket.pk,
            status__in=(
                ChainTransaction.Status.SUBMITTED,
                ChainTransaction.Status.CONFIRMED,
            ),
        ).first()
        if existing is not None:
            if existing.status == ChainTransaction.Status.CONFIRMED:
                raise ValueError("This ticket has already been refunded.")
            raise ValueError("A refund transaction is already pending for this ticket.")
        ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
            accounts__ticket_pk=ticket.pk,
            status=ChainTransaction.Status.CREATED,
        ).update(
            status=ChainTransaction.Status.FAILED,
            error_message="Superseded by a newer ticket refund transaction.",
            updated_at=timezone.now(),
        )
        record = _record_prepared_transaction(
            ChainTransaction.Instruction.REFUND_TICKET,
            ticket.event,
            owner_wallet,
            transaction,
            {
                "ticket_pk": ticket.pk,
                "ticket_type_pk": ticket.ticket_type_id,
                "purchase_pk": purchase.pk,
                "owner_wallet": owner_wallet,
                "ticket_type_id": ticket_type_id,
                "ticket_pda": str(ticket_state),
                "ticket_type_pda": str(ticket_type_state),
                "asset_address": str(asset),
                "collection_address": str(collection),
            },
            event_id,
        )

    return {
        "id": record.pk,
        "unsigned_transaction": base64.b64encode(bytes(transaction)).decode("ascii"),
        "last_valid_block_height": last_valid_block_height,
    }


def submit_finalize_event_transaction(event, valid_disputes):
    if event.on_chain_event_id is None or not event.event_pda:
        raise ValueError("The event does not have a confirmed Solana account.")
    if event.on_chain_event_id <= 0:
        raise ValueError("The on-chain event ID must be greater than zero.")
    if not 0 <= valid_disputes <= 0xFFFFFFFF:
        raise ValueError("The valid dispute count is outside the on-chain range.")

    platform = Platform.objects.filter(pk=1, is_initialized=True).first()
    if platform is None:
        raise ValueError("The Solana platform has not been initialized.")

    authority = _backend_authority_keypair()
    if str(authority.pubkey()) != platform.backend_authority_wallet:
        raise ImproperlyConfigured(
            "The configured backend keypair does not match the initialized backend authority."
        )

    event_state = Pubkey.from_string(event.event_pda)
    if event_state != _event_pda(event.on_chain_event_id):
        raise ValueError("The event account address does not match its Solana event ID.")

    instruction = Instruction(
        _program_id(),
        _anchor_discriminator("finalize_event")
        + struct.pack("<QI", event.on_chain_event_id, valid_disputes),
        [
            AccountMeta(authority.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(_platform_pda(), is_signer=False, is_writable=False),
            AccountMeta(event_state, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    rpc_client = _rpc_client()
    blockhash = rpc_client.get_latest_blockhash(commitment=Confirmed).value
    message = Message.new_with_blockhash(
        [instruction],
        authority.pubkey(),
        blockhash.blockhash,
    )
    signed_transaction = Transaction.new_unsigned(message)
    signed_transaction.sign([authority], blockhash.blockhash)
    signature = str(signed_transaction.signatures[0])

    with db_transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        existing = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
            event_id_on_chain=event.on_chain_event_id,
        ).first()
        if existing is not None:
            raise ValueError(
                f"Event finalization already has a transaction ({existing.status})."
            )

        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=signature,
            event_id_on_chain=locked_event.on_chain_event_id,
            idempotency_key=str(uuid.uuid4()),
            accounts={
                "event_pk": locked_event.pk,
                "event_pda": str(event_state),
                "backend_authority_wallet": str(authority.pubkey()),
                "valid_disputes": valid_disputes,
                "signed_transaction": base64.b64encode(
                    bytes(signed_transaction)
                ).decode("ascii"),
                "last_valid_block_height": blockhash.last_valid_block_height,
            },
        )

    rpc_client.send_raw_transaction(
        bytes(signed_transaction),
        opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
    )
    return record


def _read_on_chain_event_state(event_id, event_pda, include_event_date=False):
    event_state = Pubkey.from_string(event_pda)
    if event_id <= 0 or event_state != _event_pda(event_id):
        raise ValueError("The event account address does not match its Solana event ID.")

    account_info = _rpc_client().get_account_info(
        event_state,
        commitment=Confirmed,
    ).value
    if account_info is None or account_info.owner != _program_id():
        raise ValueError("The event account is missing or has an invalid owner.")

    account_data = bytes(account_info.data)
    discriminator = hashlib.sha256(b"account:EventState").digest()[:8]
    if len(account_data) < 105 or account_data[:8] != discriminator:
        raise ValueError("The event account data is invalid.")
    if struct.unpack_from("<Q", account_data, 8)[0] != event_id:
        raise ValueError("The event account ID does not match the requested event.")

    state = {
        "is_canceled": account_data[88] != 0,
        "is_completed": account_data[89] != 0,
        "is_active": account_data[90] != 0,
        "is_frozen": account_data[91] != 0,
    }
    if include_event_date:
        state["event_date"] = struct.unpack_from("<q", account_data, 80)[0]
    return state


def get_event_account_balance(event):
    if event.on_chain_event_id is None or not event.event_pda:
        raise ValueError("The event does not have a confirmed Solana account.")

    event_state = Pubkey.from_string(event.event_pda)
    if event_state != _event_pda(event.on_chain_event_id):
        raise ValueError("The event account address does not match its Solana event ID.")

    account_info = _rpc_client().get_account_info(
        event_state,
        commitment=Confirmed,
    ).value
    if account_info is None or account_info.owner != _program_id():
        raise ValueError("The event account is missing or has an invalid owner.")

    account_data = bytes(account_info.data)
    discriminator = hashlib.sha256(b"account:EventState").digest()[:8]
    if (
        len(account_data) < 105
        or account_data[:8] != discriminator
        or struct.unpack_from("<Q", account_data, 8)[0] != event.on_chain_event_id
    ):
        raise ValueError("The event account data is invalid.")

    rent_exempt_lamports = _rpc_client().get_minimum_balance_for_rent_exemption(
        len(account_data),
        commitment=Confirmed,
    ).value
    balance_lamports = account_info.lamports
    return {
        "event_pda": str(event_state),
        "balance_lamports": str(balance_lamports),
        "rent_exempt_lamports": str(rent_exempt_lamports),
        "withdrawable_lamports": str(max(0, balance_lamports - rent_exempt_lamports)),
    }


def submit_resolve_frozen_event_transaction(event, action_type):
    if event.on_chain_event_id is None or not event.event_pda:
        raise ValueError("The event does not have a confirmed Solana account.")
    if not isinstance(action_type, bool):
        raise ValueError("The frozen-event resolution action must be a boolean.")

    platform = Platform.objects.filter(pk=1, is_initialized=True).first()
    if platform is None:
        raise ValueError("The Solana platform has not been initialized.")

    authority = _backend_authority_keypair()
    if str(authority.pubkey()) != platform.backend_authority_wallet:
        raise ImproperlyConfigured(
            "The configured backend keypair does not match the initialized backend authority."
        )

    on_chain_state = _read_on_chain_event_state(event.on_chain_event_id, event.event_pda)
    if not on_chain_state["is_frozen"]:
        raise ValueError("The on-chain event is not frozen.")
    if on_chain_state["is_canceled"] or on_chain_state["is_completed"]:
        raise ValueError("The frozen event has already been resolved.")

    event_state = Pubkey.from_string(event.event_pda)
    instruction = Instruction(
        _program_id(),
        _anchor_discriminator("resolve_frozen_event")
        + struct.pack("<Q?", event.on_chain_event_id, action_type),
        [
            AccountMeta(authority.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(_platform_pda(), is_signer=False, is_writable=False),
            AccountMeta(event_state, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    rpc_client = _rpc_client()
    blockhash = rpc_client.get_latest_blockhash(commitment=Confirmed).value
    message = Message.new_with_blockhash(
        [instruction],
        authority.pubkey(),
        blockhash.blockhash,
    )
    signed_transaction = Transaction.new_unsigned(message)
    signed_transaction.sign([authority], blockhash.blockhash)
    signature = str(signed_transaction.signatures[0])

    with db_transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        existing = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.RESOLVE_FROZEN_EVENT,
            event_id_on_chain=locked_event.on_chain_event_id,
        ).first()
        if existing is not None:
            if (
                existing.status == ChainTransaction.Status.SUBMITTED
                and existing.accounts.get("action_type") is action_type
            ):
                if not rebroadcast_backend_transaction(existing):
                    raise ValueError(
                        "The pending resolution transaction expired. Retry the resolution "
                        "request to prepare a fresh transaction."
                    )
                return existing
            if existing.status != ChainTransaction.Status.FAILED:
                raise ValueError(
                    "A frozen-event resolution transaction already exists for a different action "
                    f"or is already {existing.status}."
                )

        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.RESOLVE_FROZEN_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=signature,
            event_id_on_chain=locked_event.on_chain_event_id,
            idempotency_key=str(uuid.uuid4()),
            accounts={
                "event_pk": locked_event.pk,
                "event_pda": str(event_state),
                "backend_authority_wallet": str(authority.pubkey()),
                "action_type": action_type,
                "signed_transaction": base64.b64encode(
                    bytes(signed_transaction)
                ).decode("ascii"),
                "last_valid_block_height": blockhash.last_valid_block_height,
            },
        )

    rpc_client.send_raw_transaction(
        bytes(signed_transaction),
        opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
    )
    return record


def rebroadcast_backend_transaction(record):
    client = _rpc_client()
    block_height = client.get_block_height(commitment=Confirmed).value
    if block_height > record.accounts["last_valid_block_height"]:
        record.status = ChainTransaction.Status.FAILED
        record.error_message = "The transaction blockhash expired before confirmation."
        record.save(update_fields=("status", "error_message", "updated_at"))
        return False

    signed_transaction = base64.b64decode(
        record.accounts["signed_transaction"],
        validate=True,
    )
    client.send_raw_transaction(
        signed_transaction,
        opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
    )
    return True


def prepare_buy_ticket_crypto_transaction(
    *,
    event_slug,
    ticket_type_pk,
    buyer_wallet,
    asset_address,
    metadata_uri,
):
    buyer = Pubkey.from_string(buyer_wallet)
    asset = Pubkey.from_string(asset_address)
    if len(metadata_uri.encode("utf-8")) > 200:
        raise ValueError("Ticket metadata URI must be at most 200 UTF-8 bytes.")

    latest_blockhash = _rpc_client().get_latest_blockhash(commitment=Confirmed).value

    with db_transaction.atomic():
        event = Event.objects.select_for_update().get(slug=event_slug)
        ticket_type = TicketType.objects.select_for_update().get(
            pk=ticket_type_pk,
            event=event,
        )

        if (
            not event.is_active
            or event.is_canceled
            or event.is_completed
            or event.is_frozen
            or timezone.now().timestamp() >= event.event_date.timestamp()
        ):
            raise ValueError("This event is not available for ticket purchases.")
        if not ticket_type.is_active or ticket_type.minted >= ticket_type.max_supply:
            raise ValueError("This ticket type is inactive or sold out.")
        if not event.event_pda or not event.collection_address or event.on_chain_event_id is None:
            raise ValueError("The event is missing confirmed on-chain account addresses.")
        if event.total_tickets_sold >= 0xFFFFFFFF:
            raise ValueError("The event has reached the on-chain ticket ID limit.")

        pending_purchase = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.BUY_TICKET_CRYPTO,
            event_id_on_chain=event.on_chain_event_id,
            status__in=(ChainTransaction.Status.CREATED, ChainTransaction.Status.SUBMITTED),
        ).exists()
        if pending_purchase:
            raise ValueError(
                "Another ticket purchase for this event is pending confirmation. Retry shortly."
            )
        if Ticket.objects.filter(asset_address=str(asset)).exists():
            raise ValueError("This asset address has already been used for a ticket.")

        ticket_id = event.total_tickets_sold + 1
        ticket_name = f"{ticket_type.name} #{ticket_id}"
        while len(ticket_name.encode("utf-8")) > 32:
            ticket_name = ticket_name[:-1]
        if not ticket_name.strip():
            ticket_name = f"Ticket #{ticket_id}"

        purchase = Purchase.objects.create(
            event=event,
            ticket_type=ticket_type,
            buyer_wallet=str(buyer),
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=ticket_type.price_in_lamports,
            status=Purchase.Status.PENDING,
        )
        ticket = Ticket.objects.create(
            event=event,
            ticket_type=ticket_type,
            owner_wallet=str(buyer),
            on_chain_ticket_id=ticket_id,
            asset_address=str(asset),
            name=ticket_name,
            metadata_uri=metadata_uri,
            price_paid_lamports=ticket_type.price_in_lamports,
            status=Ticket.Status.PENDING,
        )
        purchase.ticket = ticket
        purchase.save(update_fields=("ticket", "updated_at"))

        event_id = event.on_chain_event_id
        ticket_type_id = ticket_type.on_chain_ticket_type_id
        if ticket_type_id is None:
            raise ValueError("Ticket type has not been confirmed on Solana.")
        event_state = Pubkey.from_string(event.event_pda)
        ticket_type_state = _ticket_type_pda(event_id, ticket_type_id)
        ticket_state = _ticket_pda(str(asset))
        instruction_data = (
            _anchor_discriminator("buy_ticket_crypto")
            + struct.pack("<QQQ", event_id, ticket_type_id, ticket_id)
            + _borsh_string(ticket_name)
            + _borsh_string(metadata_uri)
        )
        instruction = Instruction(
            _program_id(),
            instruction_data,
            [
                AccountMeta(buyer, is_signer=True, is_writable=True),
                AccountMeta(asset, is_signer=True, is_writable=True),
                AccountMeta(event_state, is_signer=False, is_writable=True),
                AccountMeta(ticket_type_state, is_signer=False, is_writable=True),
                AccountMeta(ticket_state, is_signer=False, is_writable=True),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(
                    Pubkey.from_string(event.collection_address),
                    is_signer=False,
                    is_writable=True,
                ),
                AccountMeta(METAPLEX_CORE_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )
        message = Message.new_with_blockhash(
            [instruction],
            buyer,
            latest_blockhash.blockhash,
        )
        transaction = Transaction.new_unsigned(message)
        expected_message = base64.b64encode(bytes(transaction.message)).decode("ascii")
        chain_transaction = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.BUY_TICKET_CRYPTO,
            event_id_on_chain=event_id,
            asset_address=str(asset),
            idempotency_key=str(uuid.uuid4()),
            accounts={
                "event_pk": event.pk,
                "ticket_type_pk": ticket_type.pk,
                "ticket_pk": ticket.pk,
                "purchase_pk": purchase.pk,
                "buyer_wallet": str(buyer),
                "asset_address": str(asset),
                "event_pda": str(event_state),
                "ticket_type_pda": str(ticket_type_state),
                "ticket_pda": str(ticket_state),
                "collection_address": event.collection_address,
                "ticket_id": ticket_id,
                "ticket_type_id": ticket_type_id,
                "expected_message": expected_message,
            },
        )

    return {
        "purchase_id": purchase.pk,
        "ticket_id": ticket.pk,
        "chain_transaction_id": chain_transaction.pk,
        "unsigned_transaction": base64.b64encode(bytes(transaction)).decode("ascii"),
        "last_valid_block_height": latest_blockhash.last_valid_block_height,
        "ticket_asset_address": str(asset),
        "ticket_record_pda": str(ticket_state),
        "price_in_lamports": ticket_type.price_in_lamports,
        "ticket_name": ticket_name,
        "metadata_uri": metadata_uri,
    }


def submit_signed_transaction(record, encoded_transaction):
    transaction_bytes = base64.b64decode(encoded_transaction, validate=True)
    transaction = Transaction.from_bytes(transaction_bytes)
    expected_message = base64.b64decode(record.accounts["expected_message"], validate=True)
    if not _message_matches_prepared(expected_message, transaction.message):
        raise ValueError("Signed transaction does not match the transaction prepared by this server.")
    transaction.verify()

    response = _rpc_client().send_raw_transaction(
        transaction_bytes,
        opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
    )
    record.signature = str(response.value)
    record.status = ChainTransaction.Status.SUBMITTED
    record.error_message = ""
    record.save(update_fields=("signature", "status", "error_message", "updated_at"))
    return record


def confirm_submitted_transaction(record):
    if record.status == ChainTransaction.Status.CONFIRMED:
        return True
    if record.status != ChainTransaction.Status.SUBMITTED or not record.signature:
        return False

    from solders.signature import Signature

    response = _rpc_client().get_signature_statuses(
        [Signature.from_string(record.signature)],
        search_transaction_history=True,
    )
    signature_status = response.value[0]
    if signature_status is None:
        return False
    if signature_status.err is not None:
        record.status = ChainTransaction.Status.FAILED
        record.error_message = str(signature_status.err)
        record.save(update_fields=("status", "error_message", "updated_at"))
        if record.instruction == ChainTransaction.Instruction.BUY_TICKET_CRYPTO:
            Purchase.objects.filter(pk=record.accounts["purchase_pk"]).update(
                status=Purchase.Status.FAILED
            )
            Ticket.objects.filter(pk=record.accounts["ticket_pk"]).update(
                status=Ticket.Status.FAILED
            )
        elif record.instruction == ChainTransaction.Instruction.CONSUME_TICKET:
            ScanLog.objects.get_or_create(
                ticket_id=record.accounts["ticket_pk"],
                transaction_signature=record.signature,
                defaults={
                    "scanned_by_wallet": record.accounts["scanned_by_wallet"],
                    "result": ScanLog.Result.REJECTED,
                    "notes": record.error_message,
                },
            )
        return False
    if signature_status.confirmation_status not in (
        TransactionConfirmationStatus.Confirmed,
        TransactionConfirmationStatus.Finalized,
    ):
        return False

    with db_transaction.atomic():
        record = ChainTransaction.objects.select_for_update().get(pk=record.pk)
        if record.status == ChainTransaction.Status.CONFIRMED:
            return True
        if record.instruction == ChainTransaction.Instruction.INITIALIZE_PLATFORM:
            platform = Platform.objects.select_for_update().get(
                pk=record.accounts["platform_pk"]
            )
            platform.admin_wallet = record.accounts["admin_wallet"]
            platform.backend_authority_wallet = record.accounts["backend_authority_wallet"]
            platform.program_id = str(_program_id())
            platform.is_initialized = True
            platform.initialized_at = timezone.now()
            platform.save(
                update_fields=(
                    "admin_wallet",
                    "backend_authority_wallet",
                    "program_id",
                    "is_initialized",
                    "initialized_at",
                    "updated_at",
                )
            )
        elif record.instruction in (
            ChainTransaction.Instruction.CREATE_EVENT,
            ChainTransaction.Instruction.ADD_TICKET_TYPE,
        ):
            event = Event.objects.select_for_update().get(pk=record.accounts["event_pk"])
        if record.instruction == ChainTransaction.Instruction.CREATE_EVENT:
            event.on_chain_event_id = record.event_id_on_chain
            event.event_pda = record.accounts["event_pda"]
            event.collection_address = record.accounts["collection_address"]
            event.is_active = True
            event.save(
                update_fields=(
                    "on_chain_event_id",
                    "event_pda",
                    "collection_address",
                    "is_active",
                    "updated_at",
                )
            )
            from .scheduler import schedule_event_finalization

            db_transaction.on_commit(
                lambda event_pk=event.pk: schedule_event_finalization(event_pk)
            )
        elif record.instruction == ChainTransaction.Instruction.ADD_TICKET_TYPE:
            ticket_type = TicketType.objects.select_for_update().get(
                pk=record.accounts["ticket_type_pk"],
                event=event,
            )
            ticket_type.on_chain_ticket_type_id = record.accounts["ticket_type_id"]
            ticket_type.is_active = True
            ticket_type.save(
                update_fields=("on_chain_ticket_type_id", "is_active", "updated_at")
            )
        elif record.instruction == ChainTransaction.Instruction.BUY_TICKET_CRYPTO:
            event = Event.objects.select_for_update().get(pk=record.accounts["event_pk"])
            ticket_type = TicketType.objects.select_for_update().get(
                pk=record.accounts["ticket_type_pk"],
                event=event,
            )
            ticket = Ticket.objects.select_for_update().get(
                pk=record.accounts["ticket_pk"],
                event=event,
            )
            purchase = Purchase.objects.select_for_update().get(
                pk=record.accounts["purchase_pk"],
                event=event,
            )
            ticket.status = Ticket.Status.ACTIVE
            ticket.minted_at = timezone.now()
            ticket.ticket_record_pda = record.accounts["ticket_pda"]
            ticket.save(
                update_fields=("status", "minted_at", "ticket_record_pda", "updated_at")
            )
            purchase.status = Purchase.Status.COMPLETED
            purchase.transaction_signature = record.signature
            purchase.save(
                update_fields=("status", "transaction_signature", "updated_at")
            )
            event.total_tickets_sold = F("total_tickets_sold") + 1
            event.active_tickets = F("active_tickets") + 1
            event.save(update_fields=("total_tickets_sold", "active_tickets", "updated_at"))
            ticket_type.minted = F("minted") + 1
            ticket_type.save(update_fields=("minted", "updated_at"))
        elif record.instruction == ChainTransaction.Instruction.CONSUME_TICKET:
            event = Event.objects.select_for_update().get(
                pk=record.accounts["event_pk"]
            )
            ticket = Ticket.objects.select_for_update().get(
                pk=record.accounts["ticket_pk"],
                event=event,
            )
            if ticket.status != Ticket.Status.ACTIVE:
                raise ValueError(
                    "The ticket is no longer active while confirming on-chain consumption."
                )
            ticket.status = Ticket.Status.USED
            ticket.save(update_fields=("status", "updated_at"))
            event.total_ticket_scanned = F("total_ticket_scanned") + 1
            event.save(update_fields=("total_ticket_scanned", "updated_at"))
            ScanLog.objects.get_or_create(
                ticket=ticket,
                transaction_signature=record.signature,
                defaults={
                    "scanned_by_wallet": record.accounts["scanned_by_wallet"],
                    "result": ScanLog.Result.ACCEPTED,
                },
            )
            record.accounts.pop("signed_transaction", None)
        elif record.instruction == ChainTransaction.Instruction.FINALIZE_EVENT:
            event = Event.objects.select_for_update().get(
                pk=record.accounts["event_pk"],
            )
            on_chain_state = _read_on_chain_event_state(
                event.on_chain_event_id,
                record.accounts["event_pda"],
            )
            for field, value in on_chain_state.items():
                setattr(event, field, value)
            event.save(update_fields=(*on_chain_state.keys(), "updated_at"))
            from .scheduler import remove_event_finalization

            db_transaction.on_commit(
                lambda event_pk=event.pk: remove_event_finalization(event_pk)
            )
            record.accounts.pop("signed_transaction", None)
        elif record.instruction == ChainTransaction.Instruction.RESOLVE_FROZEN_EVENT:
            event = Event.objects.select_for_update().get(
                pk=record.accounts["event_pk"],
            )
            on_chain_state = _read_on_chain_event_state(
                event.on_chain_event_id,
                record.accounts["event_pda"],
            )
            if on_chain_state["is_frozen"]:
                raise ValueError("The confirmed resolution did not clear the frozen event state.")
            if record.accounts["action_type"]:
                if not on_chain_state["is_completed"] or on_chain_state["is_canceled"]:
                    raise ValueError("The on-chain event does not match the requested completion.")
            elif not on_chain_state["is_canceled"] or on_chain_state["is_completed"]:
                raise ValueError("The on-chain event does not match the requested cancellation.")

            for field, value in on_chain_state.items():
                setattr(event, field, value)
            event.save(update_fields=(*on_chain_state.keys(), "updated_at"))
            if event.is_canceled:
                from .scheduler import remove_event_finalization

                db_transaction.on_commit(
                    lambda event_pk=event.pk: remove_event_finalization(event_pk)
                )
            record.accounts.pop("signed_transaction", None)
        elif record.instruction == ChainTransaction.Instruction.CANCEL_EVENT:
            event = Event.objects.select_for_update().get(
                pk=record.accounts["event_pk"],
            )
            on_chain_state = _read_on_chain_event_state(
                event.on_chain_event_id,
                record.accounts["event_pda"],
            )
            if not on_chain_state["is_canceled"] or on_chain_state["is_active"]:
                raise ValueError("The on-chain event does not match the requested cancellation.")

            for field, value in on_chain_state.items():
                setattr(event, field, value)
            event.save(update_fields=(*on_chain_state.keys(), "updated_at"))
            from .scheduler import remove_event_finalization

            db_transaction.on_commit(
                lambda event_pk=event.pk: remove_event_finalization(event_pk)
            )
            record.accounts.pop("signed_transaction", None)
        elif record.instruction == ChainTransaction.Instruction.REFUND_TICKET:
            event = Event.objects.select_for_update().get(
                pk=record.accounts["event_pk"],
            )
            ticket = Ticket.objects.select_for_update().get(
                pk=record.accounts["ticket_pk"],
                event=event,
            )
            ticket_type = TicketType.objects.select_for_update().get(
                pk=record.accounts["ticket_type_pk"],
                event=event,
            )
            purchase = Purchase.objects.select_for_update().get(
                pk=record.accounts["purchase_pk"],
                event=event,
            )
            if (
                ticket.status != Ticket.Status.ACTIVE
                or purchase.status != Purchase.Status.COMPLETED
                or ticket_type.minted <= 0
                or event.active_tickets <= 0
            ):
                raise ValueError("The ticket refund cannot be synchronized with the current local state.")
            ticket.status = Ticket.Status.REFUNDED
            ticket.save(update_fields=("status", "updated_at"))
            purchase.status = Purchase.Status.REFUNDED
            purchase.save(update_fields=("status", "updated_at"))
            ticket_type.minted = F("minted") - 1
            ticket_type.save(update_fields=("minted", "updated_at"))
            event.active_tickets = F("active_tickets") - 1
            event.save(update_fields=("active_tickets", "updated_at"))
            record.accounts.pop("signed_transaction", None)
        record.status = ChainTransaction.Status.CONFIRMED
        record.confirmed_slot = signature_status.slot
        record.confirmed_at = timezone.now()
        record.save(
            update_fields=("status", "confirmed_slot", "confirmed_at", "accounts", "updated_at")
        )
    return True
