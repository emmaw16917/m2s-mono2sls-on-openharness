// Usage: node req_uuid.js <uuid|crypto> -- require() cost of the uuid package vs built-in crypto.randomUUID (PERF_OPTIMIZATION.md A5).
const t0=process.hrtime.bigint(); if(process.argv[2]==='uuid'){require('uuid').v1()}else{require('crypto').randomUUID()} console.log((Number(process.hrtime.bigint()-t0)/1e6).toFixed(2));
