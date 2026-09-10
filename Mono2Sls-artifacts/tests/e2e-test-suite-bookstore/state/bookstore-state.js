const {
  DynamoDBClient,
  PutItemCommand,
  ScanCommand,
  DeleteItemCommand,
} = require('@aws-sdk/client-dynamodb');
const sampleBooks = require('../fixtures/sample-books');

async function prepareTargetState(config) {
  await seedCatalog(config);
  await resetMutableState(config);
}

async function seedCatalog(config) {
  ensureConfigured(config, ['booksTable']);
  const client = createClient(config);

  for (const book of sampleBooks) {
    await client.send(new PutItemCommand({
      TableName: config.booksTable,
      Item: marshallMap(book),
    }));
  }
}

async function resetMutableState(config) {
  ensureConfigured(config, ['cartTable', 'ordersTable', 'bestsellersTable']);
  const client = createClient(config);

  await deleteAllItems(client, config.cartTable, ['customerId', 'bookId']);
  await deleteAllItems(client, config.ordersTable, ['customerId', 'orderId']);
  await deleteAllItems(client, config.bestsellersTable, ['bookId']);
}

async function deleteAllItems(client, tableName, keyNames) {
  let exclusiveStartKey;

  do {
    const scanResp = await client.send(new ScanCommand({
      TableName: tableName,
      ProjectionExpression: keyNames.join(', '),
      ExclusiveStartKey: exclusiveStartKey,
    }));

    for (const item of scanResp.Items || []) {
      const key = {};
      for (const keyName of keyNames) {
        if (item[keyName] !== undefined) {
          key[keyName] = item[keyName];
        }
      }

      if (Object.keys(key).length === keyNames.length) {
        await client.send(new DeleteItemCommand({
          TableName: tableName,
          Key: key,
        }));
      }
    }

    exclusiveStartKey = scanResp.LastEvaluatedKey;
  } while (exclusiveStartKey);
}

function createClient(config) {
  return new DynamoDBClient({ region: resolveRegion(config) });
}

function resolveRegion(config) {
  if (config.region && !String(config.region).includes('<')) {
    return config.region;
  }

  const baseUrl = String(config.baseUrl || '');
  const match = baseUrl.match(/execute-api\.([a-z0-9-]+)\.amazonaws\.com/i);
  if (match) {
    return match[1];
  }

  if (process.env.AWS_REGION) {
    return process.env.AWS_REGION;
  }

  throw new Error('Unable to resolve AWS region from target config or AWS_REGION');
}

function ensureConfigured(config, keys) {
  for (const key of keys) {
    const value = config[key];
    if (!value || String(value).includes('<')) {
      throw new Error(`Target config is incomplete: ${key}`);
    }
  }
}

function marshallMap(value) {
  const out = {};
  for (const [key, itemValue] of Object.entries(value)) {
    out[key] = marshallValue(itemValue);
  }
  return out;
}

function marshallValue(value) {
  if (value === null) {
    return { NULL: true };
  }
  if (typeof value === 'string') {
    return { S: value };
  }
  if (typeof value === 'number') {
    return { N: String(value) };
  }
  if (typeof value === 'boolean') {
    return { BOOL: value };
  }
  if (Array.isArray(value)) {
    return { L: value.map(marshallValue) };
  }
  if (typeof value === 'object') {
    return { M: marshallMap(value) };
  }
  throw new Error(`Unsupported DynamoDB value type: ${typeof value}`);
}

module.exports = {
  prepareTargetState,
  seedCatalog,
  resetMutableState,
  resolveRegion,
};
