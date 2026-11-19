use anchor_lang::prelude::*;

use crate::{
    constants::{PLATFORM_STATE_SEED},
    state::{PlatformState}
};

#[derive(Accounts)]
pub struct InitializePlatform<'info> {
    #[account(mut)]
    pub admin: Signer<'info>,

    #[account(
        init, 
        payer = admin, 
        space = 8 + PlatformState::INIT_SPACE,
        seeds = [PLATFORM_STATE_SEED],
        bump,
    )]
    pub platform_state: Account<'info, PlatformState>,

    pub system_program: Program<'info, System>,
}

pub fn handle_initialize_platform(
    ctx: Context<InitializePlatform>,
    backend_authority: Pubkey,
    // platform_fee_bps: u8,
) -> Result<()> {
    let platform_state = &mut ctx.accounts.platform_state;

    platform_state.admin = ctx.accounts.admin.key();
    platform_state.backend_authority = backend_authority;
    // platform_state.platform_fee_percentage = platform_fee_bps;
    platform_state.bump = ctx.bumps.platform_state;

    Ok(())
}