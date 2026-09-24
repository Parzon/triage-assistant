# Memory pressure and OOM kills

A container that exceeds its memory limit is killed by the kernel's OOM
killer. Workers restart, requests in flight fail, and the pattern repeats.

## Recognise an OOM kill

`docker inspect <container>` shows `OOMKilled: true`, and `dmesg` logs
"Out of memory: Killed process". A worker killed inside a container that
stays up only shows in the logs as "worker exited with signal 9".

## Stop the bleeding

Restart the affected service to recover capacity, then lower the load: reduce
the number of workers or the batch size before raising the memory limit.

## Find the leak

Compare memory over hours on the dashboard. Steady growth that never drops is
a leak; a sawtooth is normal garbage collection. Profile a live worker with
py-spy to see what allocates.
