use anchor_lang::prelude::*;
use mpl_core::instructions::{BurnV1CpiBuilder, UpdatePluginV1CpiBuilder};

use crate::{
    constants::{EVENT_SEED, TICKET_SEED, TICKET_TYPE_SEED},
    error::NftTicketsError,
    state::{EventState, TicketRecord, TicketTypeState},
};

#[derive(Accounts)]
#[instruction(event_id: u64)]
pub struct RefundTicket<'info> {
    #[account(mut)]
    pub signer: Signer<'info>,

    #[account(
        mut,
        seeds = [EVENT_SEED, event_id.to_le_bytes().as_ref()],
        bump,
    )]
    pub event_state: Account<'info, EventState>,

    #[account(
        mut,
        close = signer,
        seeds = [TICKET_SEED, asset.key().as_ref(),],
        bump,
    )]
    pub ticket: Account<'info, TicketRecord>,

    #[account(
        mut,
        seeds = [
            TICKET_TYPE_SEED,
            event_id.to_le_bytes().as_ref(),
            ticket.ticket_type_id.to_le_bytes().as_ref(),
        ],
        bump,
    )]
    pub ticket_type: Account<'info, TicketTypeState>,

    pub system_program: Program<'info, System>,

    /// CHECK: Metaplex Core validates the asset during CPI; the ticket PDA is derived from this address.
    #[account(mut)]
    pub asset: UncheckedAccount<'info>,

    /// CHECK: Tied to the event's stored collection address; Metaplex Core validates it during CPI.
    #[account(
        mut,
        address = event_state.collection_address,
    )]
    pub collection: UncheckedAccount<'info>,

    /// CHECK: Constrained to the Metaplex Core program ID before CPI invocation.
    #[account(address = mpl_core::ID)]
    pub core_program: UncheckedAccount<'info>,
}

pub fn handle_refund_ticket(ctx: Context<RefundTicket>, event_id: u64) -> Result<()> {
    let event_state = &ctx.accounts.event_state;
    let ticket = &ctx.accounts.ticket;

    require!(
        ticket.owner == ctx.accounts.signer.key(),
        NftTicketsError::RefundTicketNotTicketOwner
    );
    require!(
        ticket.event_id == event_id,
        NftTicketsError::RefundTicketEventMismatch
    );

    let current_time = Clock::get()?.unix_timestamp;

    require!(event_id > 0, NftTicketsError::RefundTicketInvalidEventId);
    require!(
        event_state.id == event_id,
        NftTicketsError::RefundTicketEventIdMismatch
    );
    require!(!ticket.is_used, NftTicketsError::RefundTicketAlreadyUsed);
    require!(
        ticket_type_matches_event(
            &ctx.accounts.ticket_type,
            event_state.id,
            ticket.ticket_type_id
        ),
        NftTicketsError::RefundTicketTypeMismatch
    );
    require!(
        !event_state.is_frozen,
        NftTicketsError::RefundTicketEventFrozen
    );

    if !event_state.is_canceled {
        let time_until_event = event_state
            .event_date
            .checked_sub(current_time)
            .ok_or(NftTicketsError::RefundTicketTimeCalculationOverflow)?;
        require!(
            time_until_event > 172800,
            NftTicketsError::RefundTicketError48Hours
        );
    }

    let amount = ticket.price_paid;
    let event_account = event_state.to_account_info();
    let required_lamports = Rent::get()?.minimum_balance(event_account.data_len());
    let withdrawable_lamports = event_account
        .lamports()
        .checked_sub(required_lamports)
        .ok_or(NftTicketsError::RefundTicketInsufficientEventFunds)?;
    require!(
        amount <= withdrawable_lamports,
        NftTicketsError::RefundTicketInsufficientEventFunds
    );

    ctx.accounts.event_state.sub_lamports(amount)?;
    ctx.accounts.signer.add_lamports(amount)?;

    let event_id_bytes = event_id.to_le_bytes();
    let signer_seeds = &[
        EVENT_SEED,
        event_id_bytes.as_ref(),
        &[ctx.bumps.event_state],
    ];

    UpdatePluginV1CpiBuilder::new(&ctx.accounts.core_program)
        .asset(&ctx.accounts.asset.to_account_info())
        .collection(Some(&ctx.accounts.collection.to_account_info()))
        .payer(&ctx.accounts.signer.to_account_info())
        .authority(Some(&ctx.accounts.event_state.to_account_info()))
        .system_program(&ctx.accounts.system_program.to_account_info())
        .plugin(mpl_core::types::Plugin::FreezeDelegate(
            mpl_core::types::FreezeDelegate { frozen: false },
        ))
        .invoke_signed(&[signer_seeds])?;

    BurnV1CpiBuilder::new(&ctx.accounts.core_program)
        .asset(&ctx.accounts.asset)
        .collection(Some(&ctx.accounts.collection.to_account_info()))
        .payer(&ctx.accounts.signer)
        .authority(Some(&ctx.accounts.signer.to_account_info()))
        .invoke()?;

    let ticket_type = &mut ctx.accounts.ticket_type;

    ticket_type.minted = ticket_type
        .minted
        .checked_sub(1)
        .ok_or(NftTicketsError::RefundTicketInvalidMintedCount)?;

    ctx.accounts.event_state.active_tickets = ctx
        .accounts
        .event_state
        .active_tickets
        .checked_sub(1)
        .ok_or(NftTicketsError::RefundTicketInvalidActiveTicketsCount)?;

    Ok(())
}

fn ticket_type_matches_event(
    ticket_type: &TicketTypeState,
    event_id: u64,
    ticket_type_id: u64,
) -> bool {
    ticket_type.event_id == event_id && ticket_type.id == ticket_type_id
}
