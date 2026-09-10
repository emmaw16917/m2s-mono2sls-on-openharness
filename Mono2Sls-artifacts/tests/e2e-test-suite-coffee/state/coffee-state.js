const {
  DynamoDBClient,
  ScanCommand,
} = require('@aws-sdk/client-dynamodb');
const {
  DynamoDBDocumentClient,
  PutCommand,
  DeleteCommand,
} = require('@aws-sdk/lib-dynamodb');

async function prepareTargetState(config, eventId) {
  ensureConfigured(config, ['configTable', 'countingTable', 'ordersTable', 'validatorTable', 'orderJourneyTable']);
  const doc = createDocClient(config);

  await putSeedConfig(doc, config, config.configTable, eventId);
  await putSeedCounter(doc, config.countingTable, eventId);
  await cleanupEventScopedState(config, eventId);
}

async function putSeedConfig(doc, config, tableName, eventId) {
  await doc.send(new PutCommand({
    TableName: tableName,
    Item: {
      PK: `config-${eventId}`,
      drinksPerBarcode: 3,
      end: '2026-12-31',
      language: 'en',
      maxOrdersInQueue: 10,
      maxOrdersPerUser: 3,
      menu: [
        {
          available: true,
          drink: 'Americano',
          icon: 'americano',
          modifiers: [
            {
              Name: 'Milk',
              Options: ['Regular', 'Oat']
            }
          ]
        },
        {
          available: true,
          drink: 'Flat White',
          icon: 'flat-white',
          modifiers: [
            {
              Name: 'Milk',
              Options: ['Regular', 'Oat']
            }
          ]
        }
      ],
      region: resolveRegion(config),
      requester: 'e2e',
      start: '2026-01-01',
      storeOpen: true,
      updatedAt: Date.now()
    }
  }));
}

async function putSeedCounter(doc, tableName, eventId) {
  await doc.send(new PutCommand({
    TableName: tableName,
    Item: {
      PK: `orderID-${eventId}`,
      IDvalue: 0,
      updatedAt: Date.now()
    }
  }));
}

async function cleanupEventScopedState(config, eventId) {
  const raw = createRawClient(config);
  const doc = createDocClient(config);
  const orderIds = [];

  await scanAndDelete(raw, doc, config.ordersTable, (item) => {
    return item.PK === 'orders' && typeof item.ORDERSTATE === 'string' && item.ORDERSTATE.startsWith(`${eventId}-`);
  }, (item) => {
    orderIds.push(item.SK);
    return { PK: item.PK, SK: item.SK };
  });

  await scanAndDelete(raw, doc, config.validatorTable, (item) => item.eventId === eventId, (item) => ({ PK: item.PK }));

  const orderIdSet = new Set(orderIds);
  await scanAndDelete(raw, doc, config.orderJourneyTable, (item) => orderIdSet.has(item.PK), (item) => ({ PK: item.PK, SK: item.SK }));
}

async function scanAndDelete(rawClient, docClient, tableName, predicate, keyBuilder) {
  let exclusiveStartKey;

  do {
    const scanResp = await rawClient.send(new ScanCommand({
      TableName: tableName,
      ExclusiveStartKey: exclusiveStartKey,
    }));

    for (const item of (scanResp.Items || []).map(unmarshallMap)) {
      if (!predicate(item)) {
        continue;
      }
      await docClient.send(new DeleteCommand({
        TableName: tableName,
        Key: keyBuilder(item),
      }));
    }

    exclusiveStartKey = scanResp.LastEvaluatedKey;
  } while (exclusiveStartKey);
}

function createRawClient(config) {
  return new DynamoDBClient({ region: resolveRegion(config) });
}

function createDocClient(config) {
  return DynamoDBDocumentClient.from(createRawClient(config), {
    marshallOptions: {
      removeUndefinedValues: true,
    }
  });
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

function unmarshallMap(item) {
  const out = {};
  for (const [key, value] of Object.entries(item || {})) {
    out[key] = unmarshallValue(value);
  }
  return out;
}

function unmarshallValue(value) {
  if (value.S !== undefined) return value.S;
  if (value.N !== undefined) return Number(value.N);
  if (value.BOOL !== undefined) return value.BOOL;
  if (value.NULL) return null;
  if (value.L) return value.L.map(unmarshallValue);
  if (value.M) return unmarshallMap(value.M);
  return undefined;
}

module.exports = {
  prepareTargetState,
  resolveRegion,
};
