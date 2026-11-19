use anchor_lang::prelude::*;

use crate::{
    constants::{EVENT_SEED, TICKET_TYPE_SEED},
    error::NftTicketsError,
    state::{EventState, TicketTypeState},
};

#[derive(Accounts)]
#[instruction(event_id: u64, ticket_type_id: u64)]
pub struct AddTicketType<'info> {
    #[account(mut)]
    pub signer: Signer<'info>,

    #[account(
        seeds = [EVENT_SEED, event_id.to_le_bytes().as_ref()],
        bump,
    )]
    pub event_state: Account<'info, EventState>,

    #[account(
        init,
        payer = signer,
        space = 8 + TicketTypeState::INIT_SPACE,
        seeds = [
            TICKET_TYPE_SEED,
            event_id.to_le_bytes().as_ref(),
            ticket_type_id.to_le_bytes().as_ref(),
        ],
        bump,
    )]
    pub ticket_type_state: Account<'info, TicketTypeState>,

    // Для чого вказується system_program !!!!!!
    pub system_program: Program<'info, System>,
}

pub fn handle_add_ticket_type(
    ctx: Context<AddTicketType>,
    event_id: u64,
    ticket_type_id: u64,
    price_in_lamports: u64,
    max_supply: u32,
) -> Result<()> {
    require!(event_id > 0, NftTicketsError::AddTicketTypeInvalidEventId);
    require!(
        ticket_type_id > 0,
        NftTicketsError::AddTicketTypeInvalidTicketTypeId
    );
    require!(
        ctx.accounts.event_state.id == event_id,
        NftTicketsError::AddTicketTypeEventIdMismatch
    );
    require!(
        price_in_lamports > 0,
        NftTicketsError::AddTicketTypeInvalidPrice
    );
    require!(
        ctx.accounts.signer.key() == ctx.accounts.event_state.organizer,
        NftTicketsError::AddTicketTypeUnauthorized
    );

    require!(max_supply > 0, NftTicketsError::AddTicketTypeInvalidSupply);

    require!(
        ctx.accounts.event_state.is_active,
        NftTicketsError::AddTicketTypeEventInactive
    );

    let ticket_type_state = &mut ctx.accounts.ticket_type_state;

    ticket_type_state.id = ticket_type_id;
    ticket_type_state.event_id = event_id;
    ticket_type_state.price_in_lamports = price_in_lamports;
    ticket_type_state.max_supply = max_supply;
    ticket_type_state.minted = 0;

    msg!("Ticket Type created: {}", ticket_type_id);
    Ok(())
}
