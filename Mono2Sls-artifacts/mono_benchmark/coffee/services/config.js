/*! Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 *  SPDX-License-Identifier: MIT-0
 */

/**
 * Config Service
 * Manages application configuration (menu, store status, etc.)
 */

const database = require('./database');

/**
 * Get config
 * GET /config?eventId=ABC
 */
async function getConfig(req, res) {
  try {
    const { eventId } = req.query;

    if (!eventId) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'Missing eventId parameter'
      });
    }

    const configKey = `config-${eventId}`;
    const config = await database.getItem('config', { PK: configKey });

    if (!config) {
      return res.status(404).json({
        error: 'Not Found',
        message: 'Config not found'
      });
    }

    res.status(200).json(config);
  } catch (error) {
    console.error('[Config] Failed to get config:', error);
    res.status(500).json({
      error: 'Internal Server Error',
      message: error.message
    });
  }
}

/**
 * Update config
 * PUT /config?eventId=ABC
 */
async function updateConfig(req, res) {
  try {
    const { eventId } = req.query;

    if (!eventId) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'Missing eventId parameter'
      });
    }

    const configKey = `config-${eventId}`;
    const existingConfig = await database.getItem('config', { PK: configKey });

    if (!existingConfig) {
      return res.status(404).json({
        error: 'Not Found',
        message: 'Config not found'
      });
    }

    const updates = req.body;
    const updatedConfig = await database.updateItem('config', { PK: configKey }, updates);

    res.status(200).json({
      message: 'Config updated',
      config: updatedConfig
    });
  } catch (error) {
    console.error('[Config] Failed to update config:', error);
    res.status(500).json({
      error: 'Internal Server Error',
      message: error.message
    });
  }
}

/**
 * Scan all configs
 * GET /config/all
 */
async function scanConfig(req, res) {
  try {
    const configs = await database.scan('config', {});
    res.status(200).json(configs);
  } catch (error) {
    console.error('[Config] Failed to scan configs:', error);
    res.status(500).json({
      error: 'Internal Server Error',
      message: error.message
    });
  }
}

module.exports = {
  getConfig,
  updateConfig,
  scanConfig
};
