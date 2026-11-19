use anchor_lang::prelude::*;
use anchor_lang::system_program::{transfer, Transfer};
use mpl_core::instructions::CreateV2CpiBuilder;

use crate::{
    constants::{EVENT_SEED, TICKET_SEED, TICKET_TYPE_SEED},
    error::NftTicketsError,
    state::{EventState, TicketRecord, TicketTypeState},
};

#[derive(Accounts)]
#[instruction(event_id: u64, ticket_type_id: u64, ticket_id: u64)]
pub struct BuyTicketCrypto<'info> {
    #[account(mut)]
    pub signer: Signer<'info>,

    #[account(mut)]
    pub asset: Signer<'info>,

    #[account(
        mut,
        seeds = [EVENT_SEED, event_id.to_le_bytes().as_ref()],
        bump,
    )]
    pub event_state: Account<'info, EventState>,

    #[account(
        mut,
        seeds = [
            TICKET_TYPE_SEED,
            event_id.to_le_bytes().as_ref(),
            ticket_type_id.to_le_bytes().as_ref(),
        ],
        bump,
    )]
    pub ticket_type: Account<'info, TicketTypeState>,

    #[account(
        init,
        payer = signer,
        space = 8 + TicketRecord::INIT_SPACE,
        seeds = [
            TICKET_SEED,
            asset.key().as_ref(),
            ],
        bump,
    )]
    pub ticket: Account<'info, TicketRecord>,

    pub system_program: Program<'info, System>,

    /// CHECK: Адресу колекції перевірено через відповідність event_state.collection_address.
    #[account(
        mut,
        address = event_state.collection_address,
    )]
    pub collection: UncheckedAccount<'info>,

    /// CHECK: Адреса обмежена програмою Metaplex Core, а акаунт використовується для CPI.
    #[account(address = mpl_core::ID)]
    pub core_program: UncheckedAccount<'info>,
}

pub fn handle_buy_ticket_crypto(
    ctx: Context<BuyTicketCrypto>,
    event_id: u64,
    ticket_type_id: u64,
    ticket_id: u64,
    name: String,
    uri: String,
) -> Result<()> {
    require!(event_id > 0, NftTicketsError::BuyTicketInvalidEventId);
    require!(
        ticket_type_id > 0,
        NftTicketsError::BuyTicketInvalidTicketTypeId
    );
    require!(ticket_id > 0, NftTicketsError::BuyTicketInvalidTicketId);
    require!(
        ctx.accounts.event_state.id == event_id,
        NftTicketsError::BuyTicketEventIdMismatch
    );
    require!(
        ctx.accounts.event_state.is_active
            && !ctx.accounts.event_state.is_canceled
            && !ctx.accounts.event_state.is_completed
            && !ctx.accounts.event_state.is_frozen,
        NftTicketsError::BuyTicketEventIsNotActive
    );
    let expected_ticket_id = ctx
        .accounts
        .event_state
        .total_tickets_sold
        .checked_add(1)
        .ok_or(NftTicketsError::BuyTicketMintedOverflow)?;
    require!(
        ticket_id == u64::from(expected_ticket_id),
        NftTicketsError::BuyTicketTicketIdAlreadyExists
    );
    require!(
        ctx.accounts.ticket_type.id == ticket_type_id
            && ctx.accounts.ticket_type.event_id == event_id,
        NftTicketsError::BuyTicketTicketTypeMismatch
    );
    require!(
        ctx.accounts.ticket_type.minted < ctx.accounts.ticket_type.max_supply,
        NftTicketsError::BuyTicketTicketTypeSoldOut
    );
    require!(!name.trim().is_empty(), NftTicketsError::BuyTicketEmptyName);
    require!(name.len() <= 32, NftTicketsError::BuyTicketNameTooLong);
    require!(!uri.trim().is_empty(), NftTicketsError::BuyTicketEmptyUri);
    require!(uri.len() <= 200, NftTicketsError::BuyTicketUriTooLong);

    let current_time = Clock::get()?.unix_timestamp;
    let event_state = &ctx.accounts.event_state;

    require!(
        current_time < event_state.event_date,
        NftTicketsError::BuyTicketEventAlreadyStarted
    );

    let from_pubkey = ctx.accounts.signer.to_account_info();
    let to_pubkey = ctx.accounts.event_state.to_account_info();

    let price = ctx.accounts.ticket_type.price_in_lamports;

    let cpi_context = CpiContext::new(
        anchor_lang::system_program::ID,
        Transfer {
            from: from_pubkey,
            to: to_pubkey,
        },
    );
    transfer(cpi_context, price)?;

    let event_id_bytes = event_id.to_le_bytes();
    let signer_seeds = &[
        EVENT_SEED,
        event_id_bytes.as_ref(),
        &[ctx.bumps.event_state],
    ];

    CreateV2CpiBuilder::new(&ctx.accounts.core_program)
        .asset(&ctx.accounts.asset.to_account_info())
        .collection(Some(&ctx.accounts.collection.to_account_info()))
        .authority(Some(&ctx.accounts.event_state.to_account_info()))
        .payer(&ctx.accounts.signer)
        .owner(Some(&ctx.accounts.signer.to_account_info()))
        .system_program(&ctx.accounts.system_program.to_account_info())
        .name(name)
        .uri(uri)
        .plugins(vec![mpl_core::types::PluginAuthorityPair {
            plugin: mpl_core::types::Plugin::FreezeDelegate(mpl_core::types::FreezeDelegate {
                frozen: true,
            }),
            authority: Some(mpl_core::types::PluginAuthority::Address {
                address: ctx.accounts.event_state.key(),
            }),
        }])
        .invoke_signed(&[signer_seeds])?;

    let ticket_type = &mut ctx.accounts.ticket_type;

    ticket_type.minted = ticket_type
        .minted
        .checked_add(1)
        .ok_or(NftTicketsError::BuyTicketMintedOverflow)?;

    let ticket = &mut ctx.accounts.ticket;
    let event = &mut ctx.accounts.event_state;

    ticket.owner = ctx.accounts.signer.key();
    ticket.id = ticket_id;
    ticket.event_id = event_id;
    ticket.is_used = false;
    ticket.price_paid = price;
    ticket.ticket_type_id = ticket_type_id;
    ticket.bump = ctx.bumps.ticket;

    event.total_tickets_sold = event
        .total_tickets_sold
        .checked_add(1)
        .ok_or(NftTicketsError::BuyTicketMintedOverflow)?;
    event.active_tickets = event
        .active_tickets
        .checked_add(1)
        .ok_or(NftTicketsError::BuyTicketActiveTicketsOverflow)?;

    Ok(())
}
