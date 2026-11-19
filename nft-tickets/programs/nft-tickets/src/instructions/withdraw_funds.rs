use anchor_lang::prelude::*;

use crate::{constants::EVENT_SEED, error::NftTicketsError, state::EventState};

#[derive(Accounts)]
#[instruction(event_id: u64)]
pub struct WithdrawFunds<'info> {
    #[account(mut)]
    pub signer: Signer<'info>,

    #[account(
        mut,
        seeds = [EVENT_SEED, event_id.to_le_bytes().as_ref()],
        bump,
    )]
    pub event_state: Account<'info, EventState>,
}

pub fn handle_withdraw_funds(ctx: Context<WithdrawFunds>, event_id: u64) -> Result<()> {
    let event_state = &ctx.accounts.event_state;

    require!(event_id > 0, NftTicketsError::WithdrawFundsInvalidEventId);
    require!(
        event_state.id == event_id,
        NftTicketsError::WithdrawFundsEventIdMismatch
    );
    require!(
        ctx.accounts.signer.key() == event_state.organizer,
        NftTicketsError::WithdrawFundsUnauthorized
    );
    require!(
        !event_state.is_canceled,
        NftTicketsError::WithdrawFundsEventCanceled
    );
    require!(
        !event_state.is_frozen,
        NftTicketsError::WithdrawFundsEventFrozen
    );
    require!(
        event_state.is_completed,
        NftTicketsError::WithdrawFundsEventNotCompleted
    );

    let current_time = Clock::get()?.unix_timestamp;
    require!(
        current_time > event_state.event_date,
        NftTicketsError::WithdrawFundsEventNotFinished
    );

    let rent = Rent::get()?;
    let event_account = event_state.to_account_info();
    let required_lamports = rent.minimum_balance(event_account.data_len());
    let current_lamports = event_account.lamports();

    let amount = current_lamports
        .checked_sub(required_lamports)
        .ok_or(NftTicketsError::WithdrawFundsInsufficientWithdrawableFunds)?;

    require!(
        amount > 0,
        NftTicketsError::WithdrawFundsInsufficientWithdrawableFunds
    );

    ctx.accounts.event_state.sub_lamports(amount)?;
    ctx.accounts.signer.add_lamports(amount)?;

    let event_state = &mut ctx.accounts.event_state;

    event_state.is_active = false;
    event_state.is_completed = true;

    Ok(())
}
