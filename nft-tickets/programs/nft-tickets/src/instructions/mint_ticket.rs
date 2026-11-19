// use anchor_lang::prelude::*;

// use crate::{constants::TICKET_SEED, state::TicketRecord};

// #[derive(Accounts)] 
// pub struct MintTicket<'info> {
//     #[account(mut)]
//     pub signer: Signer<'info>,

//     #[account(mut)]
//     pub asset: UncheckedAccount<'info>,

//     #[account(
//         init,
//         payer = signer,
//         space = 8 + TicketRecord::INIT_SPACE,
//         seeds = [TICKET_SEED, asset.key().as_ref()],
//         bump,
//     )]
//     pub ticket_state: Account<'info, TicketRecord>,

//     pub system_program: Program<'info, System>,
// }