const { DynamoDBClient, PutItemCommand } = require('@aws-sdk/client-dynamodb');

const FLIGHTS = [
  {
    id: { S: 'FL001' },
    departureDate: { S: '2025-11-10' },
    departureAirportCode: { S: 'LAX' },
    departureAirportName: { S: 'Los Angeles International' },
    departureCity: { S: 'Los Angeles' },
    departureLocale: { S: 'America/Los_Angeles' },
    arrivalDate: { S: '2025-11-10' },
    arrivalAirportCode: { S: 'SFO' },
    arrivalAirportName: { S: 'San Francisco International' },
    arrivalCity: { S: 'San Francisco' },
    arrivalLocale: { S: 'America/Los_Angeles' },
    ticketPrice: { N: '150' },
    ticketCurrency: { S: 'USD' },
    flightNumber: { N: '1001' },
    seatCapacity: { N: '100' },
    maximumSeating: { N: '100' },
  },
  {
    id: { S: 'FL002' },
    departureDate: { S: '2025-11-12' },
    departureAirportCode: { S: 'JFK' },
    departureAirportName: { S: 'John F Kennedy International' },
    departureCity: { S: 'New York' },
    departureLocale: { S: 'America/New_York' },
    arrivalDate: { S: '2025-11-12' },
    arrivalAirportCode: { S: 'LAX' },
    arrivalAirportName: { S: 'Los Angeles International' },
    arrivalCity: { S: 'Los Angeles' },
    arrivalLocale: { S: 'America/Los_Angeles' },
    ticketPrice: { N: '300' },
    ticketCurrency: { S: 'USD' },
    flightNumber: { N: '2002' },
    seatCapacity: { N: '150' },
    maximumSeating: { N: '150' },
  },
];

const TARGETS = [
  { name: 'M2S_dsv3',    table: 'dsv3-airline-flight-table',    region: 'us-west-1' },
  { name: 'M2S_sonnet46', table: 'sonnet46-airline-flights',     region: 'us-west-2' },
  { name: 'cursor',       table: 'Airline-Flight-dev',           region: 'us-west-2' },
  { name: 'claudecode',   table: 'Airline-Flight-dev',           region: 'us-west-1' },
];

async function seedTarget(target) {
  const client = new DynamoDBClient({ region: target.region });
  let ok = 0;
  let skipped = 0;

  for (const item of FLIGHTS) {
    try {
      await client.send(new PutItemCommand({
        TableName: target.table,
        Item: item,
        ConditionExpression: 'attribute_not_exists(id)',
      }));
      ok++;
    } catch (err) {
      if (err.name === 'ConditionalCheckFailedException') {
        skipped++;
      } else {
        console.error(`  [${target.name}] Failed to insert ${item.id.S}: ${err.message}`);
      }
    }
  }

  console.log(`  [${target.name}] ${target.table} (${target.region}): inserted ${ok}, already existed ${skipped}`);
}

async function main() {
  const filter = process.argv[2];
  const targets = filter
    ? TARGETS.filter((t) => t.name.toLowerCase().includes(filter.toLowerCase()))
    : TARGETS;

  if (targets.length === 0) {
    console.error('No matching targets. Available:', TARGETS.map((t) => t.name).join(', '));
    process.exit(1);
  }

  console.log(`Seeding FL001 & FL002 into ${targets.length} target(s)...\n`);

  for (const target of targets) {
    await seedTarget(target);
  }

  console.log('\nDone.');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
