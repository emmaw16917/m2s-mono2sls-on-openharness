// Writes the items of a constant array in a suite's seed script (e.g. FLIGHTS
// in seed-flights.js, already in DynamoDB JSON) into one table, using the
// suite's own AWS SDK. AWS_ENDPOINT_URL_DYNAMODB points it at DynamoDB Local.
//   node seed-from-suite.js <suiteDir> <seedFile> <ARRAY_NAME> <tableName> <region>
const fs = require('fs');
const path = require('path');

const [suiteDir, seedFile, arrayName, tableName, region] = process.argv.slice(2);
const { DynamoDBClient, PutItemCommand } = require(path.join(suiteDir, 'node_modules', '@aws-sdk', 'client-dynamodb'));

const src = fs.readFileSync(path.join(suiteDir, seedFile), 'utf8');
const start = src.indexOf(`const ${arrayName} = [`);
if (start < 0) throw new Error(`no "const ${arrayName} = [" in ${seedFile}`);
let depth = 0;
let i = src.indexOf('[', start);
const open = i;
for (; i < src.length; i += 1) {
  if (src[i] === '[') depth += 1;
  if (src[i] === ']') { depth -= 1; if (depth === 0) break; }
}
// eslint-disable-next-line no-new-func
const items = Function(`"use strict"; return (${src.slice(open, i + 1)});`)();

(async () => {
  const client = new DynamoDBClient({ region });
  for (const item of items) {
    await client.send(new PutItemCommand({ TableName: tableName, Item: item }));
  }
  console.log(`seeded ${items.length} items into ${tableName}`);
})().catch((e) => { console.error(e); process.exit(1); });
