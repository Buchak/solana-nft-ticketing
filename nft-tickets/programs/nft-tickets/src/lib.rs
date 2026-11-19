pub mod constants;
pub mod error;
pub mod instructions;
pub mod state;

use anchor_lang::prelude::*;

pub use constants::*;
pub use instructions::*;
pub use state::*;

declare_id!("Gmv9vZgZLwRjqGySw8u65MGKjnpJNiFm2Y2GeALMuRgw");

#[program]
pub mod nft_tickets {
    use super::*;

    pub fn cencel_event(ctx: Context<CencelEvent>, event_id: u64) -> Result<()> {
        handle_cencel_event(ctx, event_id)
    }

    pub fn consume_ticket(ctx: Context<ConsumeTicket>, event_id: u64) -> Result<()> {
        handle_consume_ticket(ctx, event_id)
    }

    pub fn initialize_platform(
        ctx: Context<InitializePlatform>,
        backend_authority: Pubkey,
        // platform_fee_bps: u8,
    ) -> Result<()> {
        handle_initialize_platform(ctx, backend_authority)
    }

    pub fn create_event(
        ctx: Context<CreateEvent>,
        event_id: u64,
        event_date: i64,
        name: String,
        uri: String,
    ) -> Result<()> {
        handle_create_event(ctx, event_id, event_date, name, uri)
    }

    pub fn add_ticket_type(
        ctx: Context<AddTicketType>,
        event_id: u64,
        ticket_type_id: u64,
        price_in_lamports: u64,
        max_supply: u32,
    ) -> Result<()> {
        handle_add_ticket_type(ctx, event_id, ticket_type_id, price_in_lamports, max_supply)
    }

    pub fn buy_ticket_crypto(
        ctx: Context<BuyTicketCrypto>,
        event_id: u64,
        ticket_type_id: u64,
        ticket_id: u64,
        name: String,
        uri: String,
    ) -> Result<()> {
        handle_buy_ticket_crypto(ctx, event_id, ticket_type_id, ticket_id, name, uri)
    }

    pub fn refund_ticket(ctx: Context<RefundTicket>, event_id: u64) -> Result<()> {
        handle_refund_ticket(ctx, event_id)
    }

    pub fn withdraw_funds(ctx: Context<WithdrawFunds>, event_id: u64) -> Result<()> {
        handle_withdraw_funds(ctx, event_id)
    }

    pub fn finalize_event(
        ctx: Context<FinalizeEvent>,
        event_id: u64,
        valid_disputes: u32,
    ) -> Result<()> {
        handle_finalize_event(ctx, event_id, valid_disputes)
    }

    pub fn resolve_frozen_event(
        ctx: Context<ResolveFrozenEvent>,
        event_id: u64,
        action_type: bool,
    ) -> Result<()> {
        handle_resolve_frozen_event(ctx, event_id, action_type)
    }
}