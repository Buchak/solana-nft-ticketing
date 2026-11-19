use anchor_lang::prelude::*;

use crate::{
    constants::{EVENT_SEED, PLATFORM_STATE_SEED},
    error::NftTicketsError,
    state::{EventState, PlatformState},
};

#[derive(Accounts)]
#[instruction(event_id: u64)]
pub struct ResolveFrozenEvent<'info> {
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

    pub system_program: Program<'info, System>,
}

// В подальшому можна передвати якись секретний ключ для більшої безпеки
pub fn handle_resolve_frozen_event(
    ctx: Context<ResolveFrozenEvent>,
    event_id: u64,
    action_type: bool,
) -> Result<()> {
    require!(
        ctx.accounts.signer.key() == ctx.accounts.platform_state.backend_authority,
        NftTicketsError::ResolveFrozenEventUnauthorized
    );
    require!(
        event_id > 0,
        NftTicketsError::ResolveFrozenEventInvalidEventId
    );
    require!(
        ctx.accounts.event_state.id == event_id,
        NftTicketsError::ResolveFrozenEventEventIdMismatch
    );

    let event_state = &mut ctx.accounts.event_state;
    require!(
        event_state.is_frozen,
        NftTicketsError::ResolveFrozenEventNotFrozen
    );
    require!(
        !event_state.is_canceled,
        NftTicketsError::ResolveFrozenEventAlreadyCanceled
    );
    require!(
        !event_state.is_completed,
        NftTicketsError::ResolveFrozenEventAlreadyCompleted
    );

    if action_type {
        event_state.is_completed = true;
    } else {
        event_state.is_canceled = true;
    }
    event_state.is_frozen = false;

    Ok(())
}
