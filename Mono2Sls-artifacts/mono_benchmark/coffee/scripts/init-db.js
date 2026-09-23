/**
 * DynamoDB table initialization script
 * Creates all DynamoDB tables required by the presso application
 *
 * Usage:
 *   node scripts/init-db.js
 */

require('dotenv').config();
const AWS = require('aws-sdk');

const awsConfig = {
  region: process.env.AWS_REGION || 'us-east-1'
};

if (process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY) {
  awsConfig.accessKeyId = process.env.AWS_ACCESS_KEY_ID;
  awsConfig.secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY;
}

const dynamodb = new AWS.DynamoDB(awsConfig);

const TABLE_NAMES = {
  validator: process.env.VALIDATOR_TABLE || 'presso-validator',
  config: process.env.CONFIG_TABLE || 'presso-config-table',
  orders: process.env.ORDER_TABLE || 'presso-order-table',
  counting: process.env.COUNTING_TABLE || 'presso-counting-table',
  orderJourneyEvents: process.env.ORDER_JOURNEY_TABLE || 'presso-order-journey-events',
  users: process.env.USERS_TABLE || 'presso-users'
};

async function tableExists(tableName) {
  try {
    await dynamodb.describeTable({ TableName: tableName }).promise();
    return true;
  } catch (error) {
    if (error.code === 'ResourceNotFoundException') {
      return false;
    }
    throw error;
  }
}

async function waitForTable(tableName) {
  console.log(`  Waiting for table ${tableName} to become ACTIVE...`);
  let attempts = 0;
  const maxAttempts = 30;

  while (attempts < maxAttempts) {
    try {
      const result = await dynamodb.describeTable({ TableName: tableName }).promise();
      const status = result.Table.TableStatus;

      if (status === 'ACTIVE') {
        console.log(`  OK: Table ${tableName} is ready`);
        return true;
      }

      process.stdout.write('.');
      await new Promise(resolve => setTimeout(resolve, 2000));
      attempts++;
    } catch (error) {
      console.error(`\n  FAIL: Error checking table status: ${error.message}`);
      return false;
    }
  }

  console.log(`\n  FAIL: Timed out`);
  return false;
}

async function createValidatorTable() {
  const params = {
    TableName: TABLE_NAMES.validator,
    KeySchema: [
      { AttributeName: 'PK', KeyType: 'HASH' }
    ],
    AttributeDefinitions: [
      { AttributeName: 'PK', AttributeType: 'S' }
    ],
    BillingMode: 'PAY_PER_REQUEST',
    StreamSpecification: {
      StreamEnabled: true,
      StreamViewType: 'NEW_AND_OLD_IMAGES'
    }
  };

  try {
    if (await tableExists(TABLE_NAMES.validator)) {
      console.log(`- Table ${TABLE_NAMES.validator} already exists, skipping`);
      return false;
    }

    await dynamodb.createTable(params).promise();
    console.log(`OK: Table ${TABLE_NAMES.validator} created`);
    console.log(`  Primary key: PK (String)`);
    return true;
  } catch (error) {
    if (error.code === 'ResourceInUseException') {
      console.log(`- Table ${TABLE_NAMES.validator} already exists`);
      return false;
    }
    throw error;
  }
}

async function createConfigTable() {
  const params = {
    TableName: TABLE_NAMES.config,
    KeySchema: [
      { AttributeName: 'PK', KeyType: 'HASH' }
    ],
    AttributeDefinitions: [
      { AttributeName: 'PK', AttributeType: 'S' }
    ],
    BillingMode: 'PAY_PER_REQUEST',
    StreamSpecification: {
      StreamEnabled: true,
      StreamViewType: 'NEW_AND_OLD_IMAGES'
    }
  };

  try {
    if (await tableExists(TABLE_NAMES.config)) {
      console.log(`- Table ${TABLE_NAMES.config} already exists, skipping`);
      return false;
    }

    await dynamodb.createTable(params).promise();
    console.log(`OK: Table ${TABLE_NAMES.config} created`);
    console.log(`  Primary key: PK (String)`);
    return true;
  } catch (error) {
    if (error.code === 'ResourceInUseException') {
      console.log(`- Table ${TABLE_NAMES.config} already exists`);
      return false;
    }
    throw error;
  }
}

async function createCountingTable() {
  const params = {
    TableName: TABLE_NAMES.counting,
    KeySchema: [
      { AttributeName: 'PK', KeyType: 'HASH' }
    ],
    AttributeDefinitions: [
      { AttributeName: 'PK', AttributeType: 'S' }
    ],
    BillingMode: 'PAY_PER_REQUEST'
  };

  try {
    if (await tableExists(TABLE_NAMES.counting)) {
      console.log(`- Table ${TABLE_NAMES.counting} already exists, skipping`);
      return false;
    }

    await dynamodb.createTable(params).promise();
    console.log(`OK: Table ${TABLE_NAMES.counting} created`);
    console.log(`  Primary key: PK (String)`);
    return true;
  } catch (error) {
    if (error.code === 'ResourceInUseException') {
      console.log(`- Table ${TABLE_NAMES.counting} already exists`);
      return false;
    }
    throw error;
  }
}

async function createOrdersTable() {
  const params = {
    TableName: TABLE_NAMES.orders,
    KeySchema: [
      { AttributeName: 'PK', KeyType: 'HASH' },
      { AttributeName: 'SK', KeyType: 'RANGE' }
    ],
    AttributeDefinitions: [
      { AttributeName: 'PK', AttributeType: 'S' },
      { AttributeName: 'SK', AttributeType: 'S' },
      { AttributeName: 'TS', AttributeType: 'N' },
      { AttributeName: 'ORDERSTATE', AttributeType: 'S' },
      { AttributeName: 'USERID', AttributeType: 'S' }
    ],
    BillingMode: 'PAY_PER_REQUEST',
    LocalSecondaryIndexes: [
      {
        IndexName: 'LSI-timestamp',
        KeySchema: [
          { AttributeName: 'PK', KeyType: 'HASH' },
          { AttributeName: 'TS', KeyType: 'RANGE' }
        ],
        Projection: {
          ProjectionType: 'ALL'
        }
      }
    ],
    GlobalSecondaryIndexes: [
      {
        IndexName: 'GSI-status',
        KeySchema: [
          { AttributeName: 'ORDERSTATE', KeyType: 'HASH' },
          { AttributeName: 'SK', KeyType: 'RANGE' }
        ],
        Projection: {
          ProjectionType: 'ALL'
        }
      },
      {
        IndexName: 'GSI-userId',
        KeySchema: [
          { AttributeName: 'USERID', KeyType: 'HASH' },
          { AttributeName: 'SK', KeyType: 'RANGE' }
        ],
        Projection: {
          ProjectionType: 'ALL'
        }
      }
    ]
  };

  try {
    if (await tableExists(TABLE_NAMES.orders)) {
      console.log(`- Table ${TABLE_NAMES.orders} already exists, skipping`);
      return false;
    }

    await dynamodb.createTable(params).promise();
    console.log(`OK: Table ${TABLE_NAMES.orders} created`);
    console.log(`  Partition key: PK (String), Sort key: SK (String)`);
    console.log(`  LSI: LSI-timestamp (PK, TS)`);
    console.log(`  GSI: GSI-status (ORDERSTATE, SK)`);
    console.log(`  GSI: GSI-userId (USERID, SK)`);
    return true;
  } catch (error) {
    if (error.code === 'ResourceInUseException') {
      console.log(`- Table ${TABLE_NAMES.orders} already exists`);
      return false;
    }
    throw error;
  }
}

async function createOrderJourneyEventsTable() {
  const params = {
    TableName: TABLE_NAMES.orderJourneyEvents,
    KeySchema: [
      { AttributeName: 'PK', KeyType: 'HASH' },
      { AttributeName: 'SK', KeyType: 'RANGE' }
    ],
    AttributeDefinitions: [
      { AttributeName: 'PK', AttributeType: 'S' },
      { AttributeName: 'SK', AttributeType: 'S' }
    ],
    BillingMode: 'PAY_PER_REQUEST',
    StreamSpecification: {
      StreamEnabled: true,
      StreamViewType: 'NEW_AND_OLD_IMAGES'
    }
  };

  try {
    if (await tableExists(TABLE_NAMES.orderJourneyEvents)) {
      console.log(`- Table ${TABLE_NAMES.orderJourneyEvents} already exists, skipping`);
      return false;
    }

    await dynamodb.createTable(params).promise();
    console.log(`OK: Table ${TABLE_NAMES.orderJourneyEvents} created`);
    console.log(`  Partition key: PK (String), Sort key: SK (String)`);
    return true;
  } catch (error) {
    if (error.code === 'ResourceInUseException') {
      console.log(`- Table ${TABLE_NAMES.orderJourneyEvents} already exists`);
      return false;
    }
    throw error;
  }
}

async function createUsersTable() {
  const params = {
    TableName: TABLE_NAMES.users,
    KeySchema: [
      { AttributeName: 'PK', KeyType: 'HASH' },
      { AttributeName: 'SK', KeyType: 'RANGE' }
    ],
    AttributeDefinitions: [
      { AttributeName: 'PK', AttributeType: 'S' },
      { AttributeName: 'SK', AttributeType: 'S' },
      { AttributeName: 'username', AttributeType: 'S' }
    ],
    BillingMode: 'PAY_PER_REQUEST',
    GlobalSecondaryIndexes: [
      {
        IndexName: 'GSI-username',
        KeySchema: [
          { AttributeName: 'username', KeyType: 'HASH' }
        ],
        Projection: {
          ProjectionType: 'ALL'
        }
      }
    ]
  };

  try {
    if (await tableExists(TABLE_NAMES.users)) {
      console.log(`- Table ${TABLE_NAMES.users} already exists, skipping`);
      return false;
    }

    await dynamodb.createTable(params).promise();
    console.log(`OK: Table ${TABLE_NAMES.users} created`);
    console.log(`  Partition key: PK (String), Sort key: SK (String)`);
    console.log(`  GSI: GSI-username (username)`);
    return true;
  } catch (error) {
    if (error.code === 'ResourceInUseException') {
      console.log(`- Table ${TABLE_NAMES.users} already exists`);
      return false;
    }
    throw error;
  }
}

async function main() {
  console.log('========================================');
  console.log('presso - DynamoDB Table Initialization');
  console.log('========================================');
  console.log(`Region: ${awsConfig.region}`);
  console.log('');

  try {
    console.log('Step 1: Create DynamoDB tables');
    console.log('');

    const validatorCreated = await createValidatorTable();
    const configCreated = await createConfigTable();
    const countingCreated = await createCountingTable();
    const ordersCreated = await createOrdersTable();
    const orderJourneyCreated = await createOrderJourneyEventsTable();
    const usersCreated = await createUsersTable();

    const anyCreated = validatorCreated || configCreated || countingCreated ||
                       ordersCreated || orderJourneyCreated || usersCreated;

    if (anyCreated) {
      console.log('');
      console.log('Step 2: Wait for tables to become active');
      console.log('');

      if (validatorCreated) await waitForTable(TABLE_NAMES.validator);
      if (configCreated) await waitForTable(TABLE_NAMES.config);
      if (countingCreated) await waitForTable(TABLE_NAMES.counting);
      if (ordersCreated) await waitForTable(TABLE_NAMES.orders);
      if (orderJourneyCreated) await waitForTable(TABLE_NAMES.orderJourneyEvents);
      if (usersCreated) await waitForTable(TABLE_NAMES.users);
    }

    console.log('');
    console.log('========================================');
    console.log('OK: Database initialization complete!');
    console.log('========================================');
    console.log('');
    console.log('Tables:');
    console.log(`  1. ${TABLE_NAMES.validator} - Validator`);
    console.log(`  2. ${TABLE_NAMES.config} - Config`);
    console.log(`  3. ${TABLE_NAMES.counting} - Counting`);
    console.log(`  4. ${TABLE_NAMES.orders} - Orders`);
    console.log(`  5. ${TABLE_NAMES.orderJourneyEvents} - Order Journey Events`);
    console.log(`  6. ${TABLE_NAMES.users} - Users`);
    console.log('');
    console.log('You can now start the application:');
    console.log('  npm start');
    console.log('');

  } catch (error) {
    console.error('');
    console.error('========================================');
    console.error('FAIL: Initialization failed!');
    console.error('========================================');
    console.error('Error:', error.message);
    console.error('');

    if (error.code === 'CredentialsError' || error.code === 'InvalidClientTokenId') {
      console.error('Hint: Please check your AWS credentials');
      console.error('  1. Run: aws configure');
      console.error('  2. Or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env');
    } else if (error.code === 'NetworkingError') {
      console.error('Hint: Network error. Check your connection and AWS region settings.');
    }

    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  createValidatorTable,
  createConfigTable,
  createCountingTable,
  createOrdersTable,
  createOrderJourneyEventsTable,
  createUsersTable,
  tableExists,
  main
};
