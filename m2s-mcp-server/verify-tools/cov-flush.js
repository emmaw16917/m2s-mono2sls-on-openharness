'use strict';
// Preloaded (NODE_OPTIONS=--require) into the Lambda runtime process when
// collecting coverage in an RIE container. NODE_V8_COVERAGE is only written
// when the process exits normally, which the runtime never does before the
// container is removed, so take a snapshot every 200ms instead. Each
// takeCoverage() call writes the counts since the previous one; c8 merges
// all the snapshot files.
const fs = require('fs');
const v8 = require('v8');
try { fs.mkdirSync(process.env.NODE_V8_COVERAGE, { recursive: true }); } catch (e) { /* ignore */ }
setInterval(() => { try { v8.takeCoverage(); } catch (e) { /* ignore */ } }, 200).unref();
