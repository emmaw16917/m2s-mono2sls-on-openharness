/**
 * DynamoDB Database Service
 * Uses AWS DynamoDB as the data store
 */

const AWS = require('aws-sdk');

const options = {
  region: process.env.AWS_REGION || 'us-east-1'
};

const dynamoDB = new AWS.DynamoDB.DocumentClient(options);

const TABLE_NAMES = {
  validator: process.env.VALIDATOR_TABLE || 'presso-validator',
  config: process.env.CONFIG_TABLE || 'presso-config-table',
  orders: process.env.ORDER_TABLE || 'presso-order-table',
  counting: process.env.COUNTING_TABLE || 'presso-counting-table',
  orderJourneyEvents: process.env.ORDER_JOURNEY_TABLE || 'presso-order-journey-events',
  users: process.env.USERS_TABLE || 'presso-users'
};

/**
 * Initialize database with seed data
 */
async function initialize() {
  console.log('[Database] Initializing DynamoDB data...');

  try {
    const defaultConfig = {
      PK: 'config-ABC',
      drinksPerBarcode: 10,
      end: '2022-06-22',
      language: 'pt',
      maxOrdersInQueue: 10,
      maxOrdersPerUser: 1,
      menu: [
        {
          available: true,
          drink: 'Americano',
          icon: 'barista-icons_americano-alternative',
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
          icon: 'barista-icons_flat-white-alternative@2x',
          modifiers: [
            {
              Name: 'Milk',
              Options: ['Regular', 'Oat']
            }
          ]
        },
        {
          available: false,
          drink: 'Cappuccino',
          icon: 'barista-icons_cappuccino',
          modifiers: [
            {
              Name: 'Milk',
              Options: ['Regular', 'Oat']
            }
          ]
        },
        {
          available: true,
          drink: 'Singe Espresso',
          icon: 'barista-icons_espresso-alternative',
          modifiers: []
        },
        {
          available: true,
          drink: 'Double Espresso',
          icon: 'barista-icons_espresso-alternative',
          modifiers: []
        }
      ],
      region: 'us-west-2',
      requester: 'admin',
      start: '2022-10-26',
      storeOpen: true
    };

    const existingConfig = await getItem('config', { PK: 'config-ABC' });
    if (!existingConfig) {
      await putItem('config', defaultConfig);
      console.log('[Database] - Default config created');
    } else {
      console.log('[Database] - Config already exists, skipping');
    }

    const existingCounter = await getItem('counting', { PK: 'orderID-ABC' });
    if (!existingCounter) {
      await putItem('counting', {
        PK: 'orderID-ABC',
        IDvalue: 0
      });
      console.log('[Database] - Order counter created');
    } else {
      console.log('[Database] - Order counter already exists, skipping');
    }

    console.log('[Database] DynamoDB initialization complete');
  } catch (error) {
    console.error('[Database] Initialization error:', error);
    throw error;
  }
}

/**
 * Put an item into a table
 */
async function putItem(tableName, item) {
  const table = TABLE_NAMES[tableName];
  if (!table) {
    throw new Error(`Table ${tableName} does not exist in configuration`);
  }

  const params = {
    TableName: table,
    Item: {
      ...item,
      updatedAt: Date.now()
    }
  };

  try {
    await dynamoDB.put(params).promise();
    console.log(`[Database] PUT ${tableName}: ${item.PK}${item.SK ? '#' + item.SK : ''}`);
    return item;
  } catch (error) {
    console.error(`[Database] PUT ${tableName} error:`, error);
    throw error;
  }
}

/**
 * Get an item from a table
 */
async function getItem(tableName, key) {
  const table = TABLE_NAMES[tableName];
  if (!table) {
    throw new Error(`Table ${tableName} does not exist in configuration`);
  }

  const params = {
    TableName: table,
    Key: key
  };

  try {
    const result = await dynamoDB.get(params).promise();
    const item = result.Item || null;
    console.log(`[Database] GET ${tableName}: ${key.PK}${key.SK ? '#' + key.SK : ''} - ${item ? 'found' : 'not found'}`);
    return item;
  } catch (error) {
    if (error.code === 'ResourceNotFoundException') {
      console.log(`[Database] GET ${tableName}: ${key.PK}${key.SK ? '#' + key.SK : ''} - table or item not found`);
      return null;
    }
    console.error(`[Database] GET ${tableName} error:`, error);
    throw error;
  }
}

/**
 * Update an item in a table
 */
async function updateItem(tableName, key, updates) {
  const table = TABLE_NAMES[tableName];
  if (!table) {
    throw new Error(`Table ${tableName} does not exist in configuration`);
  }

  const updateExpressionParts = [];
  const expressionAttributeNames = {};
  const expressionAttributeValues = {};

  let index = 0;
  for (const [attr, value] of Object.entries(updates)) {
    const attrName = `#attr${index}`;
    const attrValue = `:val${index}`;
    updateExpressionParts.push(`${attrName} = ${attrValue}`);
    expressionAttributeNames[attrName] = attr;
    expressionAttributeValues[attrValue] = value;
    index++;
  }

  updateExpressionParts.push(`#updatedAt = :updatedAt`);
  expressionAttributeNames['#updatedAt'] = 'updatedAt';
  expressionAttributeValues[':updatedAt'] = Date.now();

  const params = {
    TableName: table,
    Key: key,
    UpdateExpression: `SET ${updateExpressionParts.join(', ')}`,
    ExpressionAttributeNames: expressionAttributeNames,
    ExpressionAttributeValues: expressionAttributeValues,
    ReturnValues: 'ALL_NEW'
  };

  try {
    const result = await dynamoDB.update(params).promise();
    console.log(`[Database] UPDATE ${tableName}: ${key.PK}${key.SK ? '#' + key.SK : ''}`);
    return result.Attributes;
  } catch (error) {
    console.error(`[Database] UPDATE ${tableName} error:`, error);
    throw error;
  }
}

/**
 * Delete an item from a table
 */
async function deleteItem(tableName, key) {
  const table = TABLE_NAMES[tableName];
  if (!table) {
    throw new Error(`Table ${tableName} does not exist in configuration`);
  }

  const params = {
    TableName: table,
    Key: key
  };

  try {
    await dynamoDB.delete(params).promise();
    console.log(`[Database] DELETE ${tableName}: ${key.PK}${key.SK ? '#' + key.SK : ''} - success`);
    return true;
  } catch (error) {
    console.error(`[Database] DELETE ${tableName} error:`, error);
    throw error;
  }
}

/**
 * Query a table
 */
async function query(tableName, options) {
  const table = TABLE_NAMES[tableName];
  if (!table) {
    throw new Error(`Table ${tableName} does not exist in configuration`);
  }

  const { PK, SK, IndexName, ORDERSTATE, USERID, Limit = 100 } = options;

  let params = {
    TableName: table,
    Limit: Limit
  };

  if (IndexName === 'GSI-status' && ORDERSTATE) {
    params.IndexName = 'GSI-status';
    params.KeyConditionExpression = 'ORDERSTATE = :orderState';
    params.ExpressionAttributeValues = {
      ':orderState': ORDERSTATE
    };
  } else if (IndexName === 'GSI-userId' && USERID) {
    params.IndexName = 'GSI-userId';
    params.KeyConditionExpression = 'USERID = :userId';
    params.ExpressionAttributeValues = {
      ':userId': USERID
    };
  } else if (PK) {
    if (SK) {
      params.KeyConditionExpression = 'PK = :pk AND SK = :sk';
      params.ExpressionAttributeValues = {
        ':pk': PK,
        ':sk': SK
      };
    } else {
      params.KeyConditionExpression = 'PK = :pk';
      params.ExpressionAttributeValues = {
        ':pk': PK
      };
    }
  }

  try {
    const result = await dynamoDB.query(params).promise();
    console.log(`[Database] QUERY ${tableName}: found ${result.Items.length} records`);
    return result.Items || [];
  } catch (error) {
    console.error(`[Database] QUERY ${tableName} error:`, error);
    throw error;
  }
}

/**
 * Scan an entire table
 */
async function scan(tableName, options = {}) {
  const table = TABLE_NAMES[tableName];
  if (!table) {
    throw new Error(`Table ${tableName} does not exist in configuration`);
  }

  const { Limit = 100 } = options;

  const params = {
    TableName: table,
    Limit: Limit
  };

  try {
    const result = await dynamoDB.scan(params).promise();
    console.log(`[Database] SCAN ${tableName}: found ${result.Items.length} records`);
    return result.Items || [];
  } catch (error) {
    console.error(`[Database] SCAN ${tableName} error:`, error);
    throw error;
  }
}

/**
 * Atomically increment a counter
 */
async function incrementCounter(tableName, key, incrementValue = 1) {
  const table = TABLE_NAMES[tableName];
  if (!table) {
    throw new Error(`Table ${tableName} does not exist in configuration`);
  }

  const params = {
    TableName: table,
    Key: key,
    UpdateExpression: 'SET IDvalue = if_not_exists(IDvalue, :start) + :inc, updatedAt = :updatedAt',
    ExpressionAttributeValues: {
      ':inc': incrementValue,
      ':start': 0,
      ':updatedAt': Date.now()
    },
    ReturnValues: 'ALL_NEW'
  };

  try {
    const result = await dynamoDB.update(params).promise();
    const newValue = result.Attributes.IDvalue;
    console.log(`[Database] INCREMENT ${tableName}: ${key.PK}${key.SK ? '#' + key.SK : ''} -> ${newValue}`);
    return newValue;
  } catch (error) {
    console.error(`[Database] INCREMENT ${tableName} error:`, error);
    throw error;
  }
}

/**
 * Get table statistics (approximate row counts)
 */
async function getStats() {
  const stats = {};

  for (const [logicalName, tableName] of Object.entries(TABLE_NAMES)) {
    try {
      const result = await dynamoDB.scan({
        TableName: tableName,
        Select: 'COUNT'
      }).promise();
      stats[logicalName] = result.Count || 0;
    } catch (error) {
      console.error(`[Database] Failed to get stats for ${logicalName}:`, error);
      stats[logicalName] = 0;
    }
  }

  return stats;
}

/**
 * Clear all tables (for testing only)
 */
async function clearAll() {
  console.log('[Database] WARNING: Clearing all tables...');

  for (const [logicalName, tableName] of Object.entries(TABLE_NAMES)) {
    try {
      const result = await dynamoDB.scan({
        TableName: tableName
      }).promise();

      if (result.Items && result.Items.length > 0) {
        for (const item of result.Items) {
          const key = { PK: item.PK };
          if (item.SK) {
            key.SK = item.SK;
          }
          await dynamoDB.delete({
            TableName: tableName,
            Key: key
          }).promise();
        }
        console.log(`[Database] Cleared table ${logicalName}: ${result.Items.length} records`);
      }
    } catch (error) {
      console.error(`[Database] Failed to clear table ${logicalName}:`, error);
    }
  }

  console.log('[Database] All tables cleared');
}

/**
 * Get user by username (for authentication)
 */
async function getUserByUsername(username) {
  try {
    const result = await dynamoDB.query({
      TableName: TABLE_NAMES.users,
      IndexName: 'GSI-username',
      KeyConditionExpression: 'username = :username',
      ExpressionAttributeValues: {
        ':username': username
      }
    }).promise();

    return result.Items && result.Items.length > 0 ? result.Items[0] : null;
  } catch (error) {
    console.error('[Database] Failed to query user:', error);
    throw error;
  }
}

/**
 * Create a new user
 */
async function createUser(userData) {
  const userId = `user-${Date.now()}`;
  const user = {
    PK: userId,
    SK: 'profile',
    userId: userId,
    username: userData.username,
    passwordHash: userData.passwordHash,
    role: userData.role || 'user',
    createdAt: Date.now(),
    updatedAt: Date.now()
  };

  await putItem('users', user);
  return user;
}

module.exports = {
  initialize,
  putItem,
  getItem,
  updateItem,
  deleteItem,
  query,
  scan,
  incrementCounter,
  getStats,
  clearAll,
  getUserByUsername,
  createUser,
  TABLE_NAMES
};
