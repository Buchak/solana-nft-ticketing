use anchor_lang::prelude::*;

#[error_code]
pub enum NftTicketsError {
    #[msg("Ідентифікатор події повинен бути більшим за нуль.")]
    CreateEventInvalidEventId,
    #[msg("Назва події не може бути порожньою.")]
    CreateEventEmptyName,
    #[msg("Назва події не може перевищувати 32 байти.")]
    CreateEventNameTooLong,
    #[msg("URI події не може бути порожнім.")]
    CreateEventEmptyUri,
    #[msg("URI події не може перевищувати 200 байт.")]
    CreateEventUriTooLong,
    #[msg("Минулий час")]
    CreateEventIncorrectTime,

    #[msg("Ідентифікатор події повинен бути більшим за нуль.")]
    AddTicketTypeInvalidEventId,
    #[msg("Ідентифікатор типу квитка повинен бути більшим за нуль.")]
    AddTicketTypeInvalidTicketTypeId,
    #[msg("Ідентифікатор події не відповідає стану події.")]
    AddTicketTypeEventIdMismatch,
    #[msg("Ви не маєте прав для виконання цієї дії.")]
    AddTicketTypeUnauthorized,
    #[msg("Кількість квитків повинна бути більшою за нуль.")]
    AddTicketTypeInvalidSupply,
    #[msg("Цей івент неактивний або скасований.")]
    AddTicketTypeEventInactive,
    #[msg("Квиток не може бути безкоштовним")]
    AddTicketTypeInvalidPrice,

    #[msg("Ідентифікатор події повинен бути більшим за нуль")]
    BuyTicketInvalidEventId,
    #[msg("Ідентифікатор типу квитка повинен бути більшим за нуль")]
    BuyTicketInvalidTicketTypeId,
    #[msg("Ідентифікатор квитка повинен бути більшим за нуль")]
    BuyTicketInvalidTicketId,
    #[msg("Ідентифікатор події не відповідає стану події")]
    BuyTicketEventIdMismatch,
    #[msg("Тип квитка не належить цій події")]
    BuyTicketTicketTypeMismatch,
    #[msg("Подія неактивна, скасована або завершена")]
    BuyTicketEventIsNotActive,
    #[msg("Усі квитки цього типу вже продані")]
    BuyTicketTicketTypeSoldOut,
    #[msg("Назва NFT не може бути порожньою")]
    BuyTicketEmptyName,
    #[msg("Назва NFT не може перевищувати 32 байти")]
    BuyTicketNameTooLong,
    #[msg("URI NFT не може бути порожнім")]
    BuyTicketEmptyUri,
    #[msg("URI NFT не може перевищувати 200 байт")]
    BuyTicketUriTooLong,
    #[msg("Лічильник проданих квитків переповнився")]
    BuyTicketMintedOverflow,
    #[msg("Лічильник активних квитків переповнився")]
    BuyTicketActiveTicketsOverflow,
    #[msg("Ідентифікатор квитка вже зайнятий або має бути наступним за порядком")]
    BuyTicketTicketIdAlreadyExists,
    #[msg("Неможливо купити квитки у розпочатій події")]
    BuyTicketEventAlreadyStarted,

    #[msg("Ідентифікатор події повинен бути більшим за нуль")]
    CancelEventInvalidEventId,
    #[msg("Ідентифікатор події не відповідає стану події")]
    CancelEventEventIdMismatch,
    #[msg("Тільки організатор може скасувати подію")]
    CancelEventUnauthorized,
    #[msg("Подія вже неактивна")]
    CancelEventAlreadyClosed,
    #[msg("Подія вже скасована")]
    CancelEventAlreadyCanceled,
    #[msg("Не можна скасувати завершену подію")]
    CancelEventAlreadyCompleted,

    #[msg("Ідентифікатор події повинен бути більшим за нуль")]
    RefundTicketInvalidEventId,
    #[msg("Ідентифікатор події не відповідає стану події")]
    RefundTicketEventIdMismatch,
    #[msg("Квиток уже використаний і не може бути повернений")]
    RefundTicketAlreadyUsed,
    #[msg("Тип квитка не належить цій події")]
    RefundTicketTypeMismatch,
    #[msg("Кошти frozen-події заблоковані до її остаточного вирішення")]
    RefundTicketEventFrozen,
    #[msg("Не можна повернути квиток менш ніж за 48 годин до початку події")]
    RefundTicketError48Hours,
    #[msg("Не вдалося обчислити час до початку події")]
    RefundTicketTimeCalculationOverflow,
    #[msg("Кількість проданих квитків не може бути зменшена")]
    RefundTicketInvalidMintedCount,
    #[msg("Кількість активних квитків не може бути зменшена")]
    RefundTicketInvalidActiveTicketsCount,
    #[msg("На PDA події недостатньо коштів для повернення")]
    RefundTicketInsufficientEventFunds,
    #[msg("Щоб повернути кошти за квиток потрбіно бути його власником")]
    RefundTicketNotTicketOwner,
    #[msg("Квиток належить іншій події")]
    RefundTicketEventMismatch,

    #[msg("Ідентифікатор події повинен бути більшим за нуль")]
    WithdrawFundsInvalidEventId,
    #[msg("Ідентифікатор події не відповідає стану події")]
    WithdrawFundsEventIdMismatch,
    #[msg("Тільки організатор може вивести кошти")]
    WithdrawFundsUnauthorized,
    #[msg("Скасовані події призначені для повернення коштів користувачам")]
    WithdrawFundsEventCanceled,
    #[msg("Подія вже неактивна")]
    WithdrawFundsEventNotActive,
    #[msg("Подія ще не завершена")]
    WithdrawFundsEventNotCompleted,
    #[msg("Кошти frozen-події заблоковані до її остаточного вирішення")]
    WithdrawFundsEventFrozen,
    #[msg("Не можна вивести кошти до завершення події")]
    WithdrawFundsEventNotFinished,
    #[msg("На PDA недостатньо коштів понад суму, необхідну для оренди")]
    WithdrawFundsInsufficientWithdrawableFunds,

    #[msg("Ідентифікатор події повинен бути більшим за нуль")]
    FinalizeEventInvalidEventId,
    #[msg("Ідентифікатор події не відповідає стану події")]
    FinalizeEventEventIdMismatch,
    #[msg("Тільки авторизований backend може завершити подію")]
    FinalizeEventUnauthorized,
    #[msg("Подію можна завершити лише через 24 години після її початку")]
    FinalizeEventNotReady,
    #[msg("Подія вже не актуальна")]
    FinalizeEventAlreadyResolved,
    #[msg("Скарг не може бути більше проданих квитків")]
    FinalizeEventInvalidDisputesCount,

    #[msg("Ідентифікатор події повинен бути більшим за нуль")]
    ResolveFrozenEventInvalidEventId,
    #[msg("Ідентифікатор події не відповідає стану події")]
    ResolveFrozenEventEventIdMismatch,
    #[msg("Тільки авторизований backend може завершити подію")]
    ResolveFrozenEventUnauthorized,
    #[msg("Подія не перебуває у frozen-стані")]
    ResolveFrozenEventNotFrozen,
    #[msg("Подія вже скасована")]
    ResolveFrozenEventAlreadyCanceled,
    #[msg("Подія вже завершена")]
    ResolveFrozenEventAlreadyCompleted,

    #[msg("Ідентифікатор події повинен бути більшим за нуль")]
    ConsumeTicketInvalidEventId,
    #[msg("Ідентифікатор події не відповідає стану події")]
    ConsumeTicketEventIdMismatch,
    #[msg("Тільки авторизований backend може сканувати квиток")]
    ConsumeTicketUnauthorized,
    #[msg("Квиток уже використаний")]
    ConsumeTicketAlreadyUsed,
    #[msg("Квиток належить іншій події")]
    ConsumeTicketEventMismatch,
    #[msg("Подія повинна бути активною")]
    ConsumeTicketEventIsNotEctive,

    #[msg("Only the counter authority can update this counter")]
    LegacyUnauthorized,
    #[msg("Counter has reached the maximum value")]
    LegacyCounterOverflow,
}
