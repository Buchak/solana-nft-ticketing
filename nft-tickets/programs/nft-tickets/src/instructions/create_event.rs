use anchor_lang::prelude::*;
use mpl_core::instructions::CreateCollectionV2CpiBuilder;

use crate::{constants::EVENT_SEED, error::NftTicketsError, state::EventState};

#[derive(Accounts)]
#[instruction(event_id: u64)]
pub struct CreateEvent<'info> {
    #[account(mut)]
    pub signer: Signer<'info>,

    #[account(mut)]
    pub collection: Signer<'info>,

    #[account(
        init,
        payer = signer,
        space = 8 + EventState::INIT_SPACE,
        seeds = [EVENT_SEED, event_id.to_le_bytes().as_ref()],
        bump,
    )]
    pub event_state: Account<'info, EventState>,

    pub system_program: Program<'info, System>,

    /// CHECK: Адреса обмежена програмою Metaplex Core, а акаунт використовується для CPI.
    #[account(address = mpl_core::ID)]
    pub core_program: UncheckedAccount<'info>,
}

pub fn handle_create_event(
    ctx: Context<CreateEvent>,
    event_id: u64,
    event_date: i64,
    name: String,
    uri: String,
) -> Result<()> {
    require!(event_id > 0, NftTicketsError::CreateEventInvalidEventId);
    require!(
        !name.trim().is_empty(),
        NftTicketsError::CreateEventEmptyName
    );
    require!(name.len() <= 32, NftTicketsError::CreateEventNameTooLong);
    require!(!uri.trim().is_empty(), NftTicketsError::CreateEventEmptyUri);
    require!(uri.len() <= 200, NftTicketsError::CreateEventUriTooLong);
    require!(
        event_date > Clock::get()?.unix_timestamp,
        NftTicketsError::CreateEventIncorrectTime
    );

    let event_state = &mut ctx.accounts.event_state;

    event_state.id = event_id;
    event_state.organizer = ctx.accounts.signer.key();
    event_state.collection_address = ctx.accounts.collection.key();
    event_state.event_date = event_date;
    event_state.is_canceled = false;
    event_state.is_completed = false;
    event_state.is_active = true;
    event_state.is_frozen = false;
    event_state.total_tickets_sold = 0;
    event_state.total_ticket_scanned = 0;
    event_state.active_tickets = 0;
    event_state.bump = ctx.bumps.event_state;

    let event_id_bytes = event_id.to_le_bytes();
    let signer_seeds = &[
        EVENT_SEED,
        event_id_bytes.as_ref(),
        &[ctx.bumps.event_state],
    ];

    CreateCollectionV2CpiBuilder::new(&ctx.accounts.core_program)
        .collection(&ctx.accounts.collection)
        .payer(&ctx.accounts.signer)
        .update_authority(Some(&event_state.to_account_info()))
        .system_program(&ctx.accounts.system_program.to_account_info())
        .name(name)
        .uri(uri)
        .invoke_signed(&[signer_seeds])?;

    Ok(())
}
