use anchor_lang::prelude::*;

use crate::{
    constants::{EVENT_SEED, PLATFORM_STATE_SEED},
    error::NftTicketsError,
    state::{EventState, PlatformState},
};

#[derive(Accounts)]
#[instruction(event_id: u64)]
pub struct FinalizeEvent<'info> {
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

pub fn handle_finalize_event(
    ctx: Context<FinalizeEvent>,
    event_id: u64,
    valid_disputes: u32,
) -> Result<()> {
    require!(event_id > 0, NftTicketsError::FinalizeEventInvalidEventId);
    require!(
        ctx.accounts.event_state.id == event_id,
        NftTicketsError::FinalizeEventEventIdMismatch
    );
    require!(
        ctx.accounts.platform_state.backend_authority == ctx.accounts.signer.key(),
        NftTicketsError::FinalizeEventUnauthorized
    );

    let event_state = &mut ctx.accounts.event_state;
    let current_time = Clock::get()?.unix_timestamp;
    require!(
        current_time >= event_state.event_date + 86400,
        NftTicketsError::FinalizeEventNotReady
    );

    require!(
        event_state.is_active
            && !event_state.is_completed
            && !event_state.is_canceled
            && !event_state.is_frozen,
        NftTicketsError::FinalizeEventAlreadyResolved
    );

    require!(
        valid_disputes <= event_state.active_tickets,
        NftTicketsError::FinalizeEventInvalidDisputesCount
    );

    if event_state.total_tickets_sold == 0 {
        event_state.is_canceled = true;
        event_state.is_active = false;
        return Ok(());
    }

    let disputes = u64::from(valid_disputes);
    let active_tickets = u64::from(event_state.active_tickets);
    let scanned_tickets = u64::from(event_state.total_ticket_scanned);

    if scanned_tickets == 0 {
        event_state.is_canceled = true;
        event_state.is_active = false;
        return Ok(());
    }

    // 1. ОЧЕВИДНИЙ СКАМ: Якщо скарг більше 40% — це гарантована відміна,
    // незалежно від того, скільки квитків "накрутив" сканер.
    if disputes * 100 > active_tickets * 40 {
        event_state.is_canceled = true;
    }
    // 2. АНОМАЛЬНА ЯВКА: Якщо скарг мало, але прийшло менше 50% людей.
    // Заморожуємо. Можливо, це погана погода, а можливо — хитрий шахрай.
    else if scanned_tickets * 5 < active_tickets {
        event_state.is_frozen = true;
    }
    // 3. УСПІХ: Явка більше 50%, скарг менше 10%
    else if disputes * 100 < active_tickets * 10 {
        event_state.is_completed = true;
    }
    // 4. СІРА ЗОНА: Явка нормальна, але скарг від 10% до 40%
    else {
        event_state.is_frozen = true;
    }

    event_state.is_active = false;

    Ok(())
}
