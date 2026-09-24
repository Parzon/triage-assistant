# Refunds stuck in pending

Refunds normally complete within an hour. Refunds pending for longer mean the
refund worker stopped consuming its queue.

## Check the queue

Look at the refund queue depth panel. A depth that only grows means no worker
is consuming. `SELECT count(*) FROM refunds WHERE status = 'pending'` gives the
backlog.

## Restart the worker

Restart the refund-worker service. It is idempotent: every refund carries an
idempotency key, so a refund retried after a restart is never paid twice.

## Poison messages

If the queue drains and stops again at the same message, move that message to
the dead-letter queue and open a ticket with its id.
