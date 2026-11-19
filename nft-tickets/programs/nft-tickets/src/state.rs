use anchor_lang::prelude::*;

#[account]
#[derive(InitSpace)]
pub struct PlatformState {
    pub admin: Pubkey,
    pub backend_authority: Pubkey,
    // pub platform_fee_percentage: u8,
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct EventState {
    pub id: u64,
    pub organizer: Pubkey,
    pub collection_address: Pubkey,
    pub event_date: i64,
    pub is_canceled: bool,
    pub is_completed: bool,
    pub is_active: bool,
    pub is_frozen: bool,
    pub total_tickets_sold: u32,
    pub total_ticket_scanned: u32,
    pub active_tickets: u32,
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct TicketTypeState {
    pub id: u64,
    pub event_id: u64,
    pub price_in_lamports: u64,
    pub max_supply: u32,
    pub minted: u32,
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct TicketRecord {
    pub id: u64,
    pub ticket_type_id: u64,
    pub event_id: u64,
    pub is_used: bool,
    pub price_paid: u64,
    pub owner: Pubkey,
    pub bump: u8,
}



