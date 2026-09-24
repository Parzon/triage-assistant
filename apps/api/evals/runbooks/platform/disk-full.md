# Disk full on a database host

A database host at 95% disk or more stops accepting writes soon. Postgres
refuses new transactions when it cannot write its WAL.

## Check what is using space

Run `df -h` to see which filesystem is full, then `du -sh /var/lib/postgresql/*`
and `du -sh /var/log/*` to find the largest directories. Old WAL archives and
rotated logs are the usual culprits.

## Free space

Delete WAL archives only after confirming the latest base backup succeeded:
`pgbackrest info` must show a backup newer than the archives you delete. Then
rotate and compress logs with `logrotate -f /etc/logrotate.conf`. Never delete
files inside the data directory by hand.

## Expand the volume

If the disk is still over 90% after freeing space, grow the volume in the
cloud console, then grow the filesystem online with `resize2fs /dev/nvme1n1`
(ext4) or `xfs_growfs /var/lib/postgresql` (XFS). No restart is needed.
