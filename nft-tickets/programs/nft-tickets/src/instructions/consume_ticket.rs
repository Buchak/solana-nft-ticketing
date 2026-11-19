use anchor_lang::prelude::*;

use crate::{
    constants::{EVENT_SEED, PLATFORM_STATE_SEED, TICKET_SEED},
    error::NftTicketsError,
    state::{EventState, PlatformState, TicketRecord},
};

#[derive(Accounts)]
#[instruction(event_id: u64)]
pub struct ConsumeTicket<'info> {
    #[account(mut)]
    pub signer: Signer<'info>,

    #[account(
        seeds = [PLATFORM_STATE_SEED],
        bump,
    )]
    pub platform_state: Account<'info, PlatformState>,

    #[account(
        mut,
        seeds = [EVENT_SEED, event_id.to_le_bytes().as_ref()],
        bump,
    )]
    pub event_state: Account<'info, EventState>,

    #[account(
        mut, 
        seeds = [TICKET_SEED, asset.key().as_ref()],
        bump = ticket.bump,
    )]
    pub ticket: Account<'info, TicketRecord>,

    /// CHECK: Ключ акаунта використовується в PDA-seed квитка; відповідність перевіряє seeds-constraint на ticket.
    pub asset: UncheckedAccount<'info>,
}

pub fn handle_consume_ticket(ctx: Context<ConsumeTicket>, event_id: u64) -> Result<()> {
    require!(event_id > 0, NftTicketsError::ConsumeTicketInvalidEventId);
    require!(
        ctx.accounts.event_state.id == event_id,
        NftTicketsError::ConsumeTicketEventIdMismatch
    );
    require!(
        ctx.accounts.platform_state.backend_authority == ctx.accounts.signer.key(),
        NftTicketsError::ConsumeTicketUnauthorized
    );
    require!(
        !ctx.accounts.ticket.is_used,
        NftTicketsError::ConsumeTicketAlreadyUsed
    );
    require!(
        ctx.accounts.event_state.is_active,
        NftTicketsError::ConsumeTicketEventIsNotEctive
    );

    let event_state = &mut ctx.accounts.event_state;
    let ticket = &mut ctx.accounts.ticket;

    require!(
        ticket.event_id == event_id,
        NftTicketsError::ConsumeTicketEventMismatch
    );

    ticket.is_used = true;
    event_state.total_ticket_scanned = event_state.total_ticket_scanned.checked_add(1).unwrap();

    Ok(())
}
