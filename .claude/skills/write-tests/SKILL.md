---
name: write-tests
description: Write tests worth keeping, that fail on a real bug and survive a refactor. Use when writing, fixing or reviewing tests, when adding a feature or fixing a bug, or when asked to work test first.
---

# Write tests

A test is worth keeping when it fails on a real bug and still passes after
a refactor that kept the behaviour.

## What to test

- Test through the public interface: the endpoint, the function others
  call, the page. Not private helpers, not internal calls.
- Name the interfaces you will test first, and for each one what a test
  there catches and what it misses. Spend tests where a bug costs most and
  the logic is hardest, not on every edge.
- Cover the failure paths as well as the happy one: invalid input, not
  found, not allowed, a dependency down or slow. `AGENTS.md` may list the
  ones this repository requires.

## How to write one

- Expected values come from outside the code: a worked example, the spec,
  a value worked out by hand. A test that computes the answer the way the
  code does (`expect(add(a, b)).toBe(a + b)`) can never fail.
- Check the result through the interface. Don't reach around it: no
  reading the database to check what the API's create did, when the API can
  read it back.
- Mock only what you don't own or can't run: a third-party API, the clock.
  Mocking your own code ties the test to how the code is built.
- Name the test for the behaviour: "an expired session is refused", not
  "test_session_3".
- Same result on every run: a fixed clock, a fixed seed, no network.
- Wait for a condition, never for a fixed time.

## The loop

1. Write one test. Run it: it fails, and for the right reason (the
   assertion, not an import error).
2. Write the least code that makes it pass. Run it: it passes.
3. Then the next test. Not all the tests first: tests written ahead check
   the shape you imagined, not the behaviour you built.

Tidy the code once the tests are green, and run them again.

## A bug fix

The test reproduces the bug. Prove that it catches it: run it with the fix
reverted (it fails), then with the fix (it passes).

## Signs of a bad test, when reviewing

- It breaks on a refactor that kept the behaviour: it is tied to the
  implementation.
- It can't fail: it recomputes the answer, checks that a mock returned what
  it was told to, or asserts nothing.
- It passes alone and fails in the suite, or the other way round: shared
  state between tests.
- It sleeps for a fixed time.
