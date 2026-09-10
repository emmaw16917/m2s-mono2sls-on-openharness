const fs = require('fs');
const path = require('path');
const {
  DynamoDBClient,
  PutItemCommand,
  ScanCommand,
  DeleteItemCommand,
} = require('@aws-sdk/client-dynamodb');

async function prepareTargetState(config, target) {
  await resetMutableState(config);
  return seedProducts(config, target);
}

async function seedProducts(config, target) {
  ensureConfigured(config, ['tableName']);
  const client = createClient(config);
  const runtimeState = buildRuntimeState(target);

  for (const product of runtimeState.products) {
    await client.send(new PutItemCommand({
      TableName: config.tableName,
      Item: marshallMap({
        pk: `PRODUCT#${product.id}`,
        sk: 'DETAIL',
        id: product.id,
        name: product.name,
        description: product.description,
        category: product.category,
        price: product.price,
        image: product.image,
        created_at: runtimeState.generatedAt,
      }),
    }));

    await client.send(new PutItemCommand({
      TableName: config.tableName,
      Item: marshallMap({
        pk: `PRODUCT#${product.id}`,
        sk: 'TOTAL',
        total_quantity: 0,
        updated_at: runtimeState.generatedAt,
      }),
    }));
  }

  writeRuntimeState(target, runtimeState);
  return runtimeState;
}

async function resetMutableState(config) {
  ensureConfigured(config, ['tableName']);
  const client = createClient(config);
  let exclusiveStartKey;

  do {
    const scanResp = await client.send(new ScanCommand({
      TableName: config.tableName,
      ProjectionExpression: 'pk, sk',
      ExclusiveStartKey: exclusiveStartKey,
    }));

    for (const item of scanResp.Items || []) {
      const pk = item.pk && item.pk.S;
      const sk = item.sk && item.sk.S;

      if (!pk || !sk) {
        continue;
      }

      if (shouldDeleteItem(pk, sk)) {
        await client.send(new DeleteItemCommand({
          TableName: config.tableName,
          Key: {
            pk: { S: pk },
            sk: { S: sk },
          },
        }));
      }
    }

    exclusiveStartKey = scanResp.LastEvaluatedKey;
  } while (exclusiveStartKey);
}

function shouldDeleteItem(pk, sk) {
  return (
    pk.startsWith('PRODUCT#e2e-') ||
    pk.startsWith('USER#e2e-') ||
    sk.startsWith('product#e2e-')
  );
}

function buildRuntimeState(target) {
  const shortTarget = String(target || 'target')
    .replace(/[^a-z0-9]+/gi, '-')
    .toLowerCase();
  const runId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const productPrefix = `e2e-${shortTarget}-${runId}`;
  const generatedAt = new Date().toISOString();

  return {
    target,
    runId,
    generatedAt,
    fixtures: {
      primaryProductId: `${productPrefix}-primary`,
      secondaryProductId: `${productPrefix}-secondary`,
      tertiaryProductId: `${productPrefix}-tertiary`,
      quaternaryProductId: `${productPrefix}-quaternary`,
    },
    products: [
      {
        id: `${productPrefix}-primary`,
        name: 'E2E Primary Product',
        description: 'Primary product for anonymous cart CRUD',
        category: 'testing',
        price: 19.99,
        image: 'https://example.com/e2e-primary.png',
      },
      {
        id: `${productPrefix}-secondary`,
        name: 'E2E Secondary Product',
        description: 'Secondary product for cart migration visibility',
        category: 'testing',
        price: 29.99,
        image: 'https://example.com/e2e-secondary.png',
      },
      {
        id: `${productPrefix}-tertiary`,
        name: 'E2E Tertiary Product',
        description: 'Tertiary product for checkout flow',
        category: 'testing',
        price: 39.99,
        image: 'https://example.com/e2e-tertiary.png',
      },
      {
        id: `${productPrefix}-quaternary`,
        name: 'E2E Quaternary Product',
        description: 'Quaternary product for migration aggregate invariants',
        category: 'testing',
        price: 49.99,
        image: 'https://example.com/e2e-quaternary.png',
      },
    ],
  };
}

function writeRuntimeState(target, runtimeState) {
  const runtimeDir = path.resolve(__dirname, 'runtime');
  if (!fs.existsSync(runtimeDir)) {
    fs.mkdirSync(runtimeDir, { recursive: true });
  }

  fs.writeFileSync(
    path.join(runtimeDir, `${target}.json`),
    JSON.stringify(runtimeState, null, 2)
  );
}

function loadRuntimeState(target) {
  const runtimePath = path.resolve(__dirname, 'runtime', `${target}.json`);
  if (!fs.existsSync(runtimePath)) {
    throw new Error(`Runtime state not found for ${target}. Run prepareTarget() first.`);
  }
  return JSON.parse(fs.readFileSync(runtimePath, 'utf-8'));
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
  seedProducts,
  resetMutableState,
  loadRuntimeState,
  resolveRegion,
};
