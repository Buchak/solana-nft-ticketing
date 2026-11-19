use anchor_lang::prelude::*;

use crate::{constants::EVENT_SEED, error::NftTicketsError, state::EventState};

#[derive(Accounts)]
#[instruction(event_id: u64)]
pub struct CencelEvent<'info> {
    #[account(mut)]
    pub signer: Signer<'info>,

    #[account(
        mut,
        seeds = [EVENT_SEED, event_id.to_le_bytes().as_ref()],
        bump,
    )]
    pub event_state: Account<'info, EventState>,

    pub system_program: Program<'info, System>,
}

pub fn handle_cencel_event(ctx: Context<CencelEvent>, event_id: u64) -> Result<()> {
    let event_state = &mut ctx.accounts.event_state;

    require!(event_id > 0, NftTicketsError::CancelEventInvalidEventId);
    require!(
        event_state.id == event_id,
        NftTicketsError::CancelEventEventIdMismatch
    );
    require!(
        ctx.accounts.signer.key() == event_state.organizer,
        NftTicketsError::CancelEventUnauthorized
    );
    require!(
        event_state.is_active,
        NftTicketsError::CancelEventAlreadyClosed
    );
    require!(
        !event_state.is_canceled,
        NftTicketsError::CancelEventAlreadyCanceled
    );
    require!(
        !event_state.is_completed,
        NftTicketsError::CancelEventAlreadyCompleted
    );

    event_state.is_canceled = true;
    event_state.is_active = false;

    Ok(())
}
