'use strict';
// GENERIC SKELETON -- not a finished, self-contained mock. The interception
// mechanism below (patch .send() on the SDK client prototypes, dispatch by
// command.constructor.name, add a fixed artificial delay) is reusable across
// any migrated codebase. The *coverage table* (which Command classes have a
// mock response, which Client prototypes get patched, what event shape each
// handler needs to pass its own validation) is NOT reusable -- it depends on
// what a given monolith's generated code actually uses, and must be scanned
// and supplied fresh for every run. See PERF_OPTIMIZATION_DESIGN.md §3 step 1
// and §4 item 5.
//
// Usage per run:
//   1. grep -rho "new [A-Za-z]*Command(" output/lambdas/ to find every
//      Command class actually used, and which @aws-sdk/client-* packages are
//      require()'d, to know which client prototypes need patching.
//   2. Build a `responseTable` covering every Command class found (see the
//      STARTER_RESPONSE_TABLE below for the shape -- it is an example from
//      one project, NOT a claim of completeness for a new one).
//   3. installShim({ clients: [...], responseTable }) before requiring any
//      handler file.
//   4. An uncovered Command class throws loudly (by design -- see
//      mockResponseFor) rather than silently returning `undefined`, so a gap
//      in the coverage table shows up as a crash, not as a falsely-fast
//      measurement.

const DEFAULT_MOCK_LATENCY_MS = 8;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mockResponseFor(command, responseTable) {
  const name = command.constructor.name;
  if (!(name in responseTable)) {
    throw new Error(
      `mock-sdk-shim: no mock response registered for "${name}". ` +
        'Scan the codebase (see file header) and add it to responseTable ' +
        '-- do not assume the last run\'s coverage table still applies.'
    );
  }
  const entry = responseTable[name];
  return typeof entry === 'function' ? entry(command) : entry;
}

/**
 * Patches `.send()` on every client prototype in `clients` to dispatch through
 * `responseTable`. Each client's own module must already be require()'d by
 * the caller (see usage note above) so patching its prototype here is visible
 * to any handler that later does `require('@aws-sdk/...')` and constructs its
 * own instance -- Node's require cache guarantees they share the same class.
 *
 * @param {object} opts
 * @param {Function[]} opts.clientPrototypes - e.g. [DynamoDBDocumentClient, SQSClient]
 * @param {Record<string, any>} opts.responseTable - Command class name -> mock
 *        response (or a function(command) -> response for cases that need to
 *        vary by input, e.g. GetCommand returning different items by Key).
 * @param {number} [opts.latencyMs] - artificial per-call delay; see file
 *        header, this is a disclosed proxy for a single-item network round
 *        trip, not a real measurement.
 */
function installShim({ clientPrototypes, responseTable, latencyMs = DEFAULT_MOCK_LATENCY_MS }) {
  if (!clientPrototypes || clientPrototypes.length === 0) {
    throw new Error('mock-sdk-shim: installShim() requires at least one client prototype to patch.');
  }
  if (!responseTable) {
    throw new Error('mock-sdk-shim: installShim() requires a responseTable (see file header).');
  }

  for (const ClientClass of clientPrototypes) {
    ClientClass.prototype.send = async function (command) {
      callLog.push(command.constructor.name);
      const interval = { start: process.hrtime.bigint(), end: null };
      intervals.push(interval);
      await sleep(latencyMs);
      interval.end = process.hrtime.bigint();
      return mockResponseFor(command, responseTable);
    };
  }
}

let callLog = [];
let intervals = [];
function resetCallLog() {
  callLog = [];
  intervals = [];
}
function getCallLog() {
  return callLog.slice();
}

// Sequential round-trip depth: the longest chain of calls where each one
// starts only after the previous one finished (i.e. the number of network
// round trips on the critical path). Unlike wall-clock ms this does not depend
// on timer granularity (setTimeout(8) is ~15.6ms on Windows) or machine load,
// so it is the deterministic metric to judge batch/parallel/aggregate rewrites.
function getCriticalPathDepth() {
  const done = intervals.filter((i) => i.end !== null).sort((a, b) => (a.start < b.start ? -1 : 1));
  const depth = done.map(() => 1);
  for (let j = 0; j < done.length; j++) {
    for (let i = 0; i < j; i++) {
      if (done[j].start >= done[i].end && depth[i] + 1 > depth[j]) depth[j] = depth[i] + 1;
    }
  }
  return depth.length ? Math.max(...depth) : 0;
}

// Example coverage table from the bookstore-oh run (2026-09-23) -- kept only
// as a worked example of the shape each entry takes. A new run must verify
// this still matches its own codebase's Command usage before reusing it, per
// the usage note above; do not assume it is complete for a different project.
const EXAMPLE_RESPONSE_TABLE_FROM_BOOKSTORE = {
  GetCommand: { Item: { id: 'mock-id', name: 'Mock Item', price: 9.99 } },
  QueryCommand: { Items: [{ id: 'mock-1' }, { id: 'mock-2' }], Count: 2 },
  ScanCommand: { Items: [{ id: 'mock-1' }, { id: 'mock-2' }], Count: 2 },
  PutCommand: {},
  DeleteCommand: {},
  UpdateCommand: { Attributes: { salesCount: 1 } },
  SendMessageCommand: { MessageId: 'mock-message-id' },
};

module.exports = {
  installShim,
  resetCallLog,
  getCallLog,
  getCriticalPathDepth,
  DEFAULT_MOCK_LATENCY_MS,
  EXAMPLE_RESPONSE_TABLE_FROM_BOOKSTORE,
};
