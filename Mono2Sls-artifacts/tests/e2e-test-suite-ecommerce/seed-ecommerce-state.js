const path = require('path');
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');

const target = process.argv[2] || process.env.TARGET;
if (!target) {
  throw new Error('Usage: node seed-ecommerce-state.js <target>');
}

const config = require(path.resolve(__dirname, `config/${target}.json`));
const contract = require(path.resolve(__dirname, 'config/normalized_business_contract.json'));

const region = process.env.AWS_REGION || config.region;
const tableName = process.env.PRODUCTS_TABLE || config.productsTable;

if (!tableName || String(tableName).includes('<')) {
  throw new Error('Products table name is not configured. Set PRODUCTS_TABLE or update target config.');
}

if (!region || String(region).includes('<')) {
  throw new Error('AWS region is not configured. Set AWS_REGION or update target config.');
}

const client = new DynamoDBClient({ region });
const docClient = DynamoDBDocumentClient.from(client);

async function main() {
  const now = new Date().toISOString();
  const products = Object.values(contract.fixtures.products);

  for (const product of products) {
    await docClient.send(new PutCommand({
      TableName: tableName,
      Item: {
        ...product,
        tags: [product.category.toLowerCase(), product.name.toLowerCase().replace(/\s+/g, '-')],
        pictures: [],
        createdDate: now,
        modifiedDate: now,
      },
    }));
  }

  console.log(`Seeded ${products.length} products into ${tableName}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
