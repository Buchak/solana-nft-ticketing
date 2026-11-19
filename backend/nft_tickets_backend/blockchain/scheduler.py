import logging
import os
import sys
import threading
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
import httpx
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from solana.rpc.core import RPCException


logger = logging.getLogger(__name__)
# Temporary scheduler; replace with Celery + Redis before scaling the backend.
_scheduler = BackgroundScheduler(timezone="UTC")
_scheduler_lock = threading.Lock()


def start_event_scheduler():
    if "test" in sys.argv:
        return
    if "runserver" in sys.argv and sys.argv[1:2] == ["runserver"]:
        if os.environ.get("RUN_MAIN") != "true":
            return

    with _scheduler_lock:
        if _scheduler.running:
            return
        _scheduler.start()
        _scheduler.add_job(
            reconcile_event_finalization_jobs,
            trigger="interval",
            seconds=60,
            id="reconcile-event-finalization-jobs",
            replace_existing=True,
            next_run_time=timezone.now() + timedelta(seconds=5),
        )


def schedule_event_finalization(event_id, run_date=None):
    if not _scheduler.running:
        return

    from blockchain.models import ChainTransaction
    from events.models import Event

    event = Event.objects.filter(pk=event_id).first()
    if event is None or not _is_finalizable(event):
        remove_event_finalization(event_id)
        return

    existing = ChainTransaction.objects.filter(
        instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
        event_id_on_chain=event.on_chain_event_id,
    ).first()
    if existing is not None:
        if existing.status in (
            ChainTransaction.Status.CONFIRMED,
            ChainTransaction.Status.FAILED,
        ):
            remove_event_finalization(event_id)
            return

    scheduled_time = run_date or (event.event_date + timedelta(hours=24))
    job_id = _event_job_id(event_id)
    current_job = _scheduler.get_job(job_id)
    if (
        existing is not None
        and existing.status == ChainTransaction.Status.SUBMITTED
        and current_job is not None
        and run_date is None
    ):
        return
    if (
        existing is not None
        and existing.status == ChainTransaction.Status.SUBMITTED
        and run_date is None
    ):
        scheduled_time = timezone.now() + timedelta(seconds=30)
    if current_job:
        if current_job.next_run_time == scheduled_time:
            return
        if run_date is None and scheduled_time <= timezone.now():
            return
    if scheduled_time <= timezone.now():
        scheduled_time = timezone.now() + timedelta(seconds=1)
    _scheduler.add_job(
        run_event_finalization,
        trigger="date",
        run_date=scheduled_time,
        args=(event_id,),
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "Scheduled event %s finalization for %s",
        event_id,
        scheduled_time.isoformat(),
    )


def remove_event_finalization(event_id):
    if not _scheduler.running:
        return
    job_id = _event_job_id(event_id)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        logger.info("Removed finalization job for event %s", event_id)


def reconcile_event_finalization_jobs():
    from events.models import Event

    eligible_events = Event.objects.filter(
        is_active=True,
        is_canceled=False,
        is_completed=False,
        is_frozen=False,
        on_chain_event_id__isnull=False,
    ).exclude(event_pda="").values_list("pk", flat=True)
    for event_id in eligible_events.iterator():
        try:
            schedule_event_finalization(event_id)
        except (ImproperlyConfigured, ValueError, RPCException, httpx.HTTPError, OSError):
            logger.exception("Could not schedule finalization for event %s", event_id)


def run_event_finalization(event_id):
    from blockchain.management.commands.finalize_due_events import Command
    from blockchain.models import ChainTransaction
    from events.models import Event, EventDispute

    event = Event.objects.filter(pk=event_id).first()
    if event is None or not _is_finalizable(event):
        return

    due_at = event.event_date + timedelta(hours=24)
    if due_at > timezone.now():
        schedule_event_finalization(event_id, due_at)
        return

    finalization = ChainTransaction.objects.filter(
        instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
        event_id_on_chain=event.on_chain_event_id,
    ).first()
    if finalization and finalization.status in (
        ChainTransaction.Status.CONFIRMED,
        ChainTransaction.Status.FAILED,
    ):
        return

    retry_delay = timedelta(minutes=5)
    try:
        Command()._finalize_event(event_id)
    except (ImproperlyConfigured, ValueError, RPCException, httpx.HTTPError, OSError):
        logger.exception("Event %s finalization attempt failed", event_id)
        schedule_event_finalization(event_id, timezone.now() + retry_delay)
        return

    event.refresh_from_db()
    if not _is_finalizable(event):
        return

    finalization = ChainTransaction.objects.filter(
        instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
        event_id_on_chain=event.on_chain_event_id,
    ).first()
    if finalization and finalization.status in (
        ChainTransaction.Status.CONFIRMED,
        ChainTransaction.Status.FAILED,
    ):
        return

    if EventDispute.objects.filter(
        event=event,
        status=EventDispute.Status.PENDING,
    ).exists():
        retry_delay = timedelta(hours=1)
    schedule_event_finalization(event_id, timezone.now() + retry_delay)


def _is_finalizable(event):
    return bool(
        event.is_active
        and not event.is_canceled
        and not event.is_completed
        and not event.is_frozen
        and event.on_chain_event_id is not None
        and event.event_pda
    )


def _event_job_id(event_id):
    return f"finalize-event-{event_id}"
