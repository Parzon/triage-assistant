# Roll back a deploy

When errors start right after a deploy, roll back first and investigate after:
a rollback takes minutes, a diagnosis under pressure takes longer.

## Decide

Compare the error rate before and after the deploy's timestamp on the service
dashboard. A step change within minutes of the deploy is enough to act on.

## Roll back

Run `make deploy tag=<previous tag>` on the host. It keeps serving during the
switch, and skips database migrations when the schema is newer than the
release, which is safe for expand/contract migrations.

## After the rollback

Confirm the error rate returned to its baseline, then open an incident note
with the bad tag, and block it from being deployed again until it is fixed.
