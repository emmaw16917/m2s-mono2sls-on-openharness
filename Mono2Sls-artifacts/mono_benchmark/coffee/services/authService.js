/**
 * Authentication Service
 * Handles user registration, login, and JWT token management
 */

const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const database = require('./database');

const JWT_SECRET = process.env.JWT_SECRET || 'your-secret-key-change-this-in-production';
const JWT_EXPIRES_IN = process.env.JWT_EXPIRES_IN || '24h';

/**
 * Generate a JWT token
 */
function generateToken(userId, username, role = 'user') {
  return jwt.sign(
    {
      userId: userId,
      username: username,
      role: role,
      iat: Math.floor(Date.now() / 1000)
    },
    JWT_SECRET,
    { expiresIn: JWT_EXPIRES_IN }
  );
}

/**
 * Verify a JWT token
 */
function verifyToken(token) {
  try {
    return jwt.verify(token, JWT_SECRET);
  } catch (error) {
    return null;
  }
}

/**
 * User registration
 */
async function register(req, res) {
  try {
    const { username, password, role } = req.body;

    if (!username || !password) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'Username and password are required'
      });
    }

    if (username.length < 3) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'Username must be at least 3 characters'
      });
    }

    if (password.length < 6) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'Password must be at least 6 characters'
      });
    }

    const existingUser = await database.getUserByUsername(username);
    if (existingUser) {
      return res.status(409).json({
        error: 'Conflict',
        message: 'Username already exists'
      });
    }

    const saltRounds = 10;
    const passwordHash = await bcrypt.hash(password, saltRounds);

    const user = await database.createUser({
      username: username,
      passwordHash: passwordHash,
      role: role || 'user'
    });

    const token = generateToken(user.userId, user.username, user.role);

    console.log(`[AuthService] User registered: ${username}`);

    res.status(201).json({
      message: 'Registration successful',
      token: token,
      user: {
        userId: user.userId,
        username: user.username,
        role: user.role
      }
    });
  } catch (error) {
    console.error('[AuthService] Registration error:', error);
    res.status(500).json({
      error: 'Internal Server Error',
      message: 'Registration failed, please try again later'
    });
  }
}

/**
 * User login
 */
async function login(req, res) {
  try {
    const { username, password } = req.body;

    if (!username || !password) {
      return res.status(400).json({
        error: 'Bad Request',
        message: 'Username and password are required'
      });
    }

    const user = await database.getUserByUsername(username);
    if (!user) {
      return res.status(401).json({
        error: 'Unauthorized',
        message: 'Invalid username or password'
      });
    }

    const isPasswordValid = await bcrypt.compare(password, user.passwordHash);
    if (!isPasswordValid) {
      return res.status(401).json({
        error: 'Unauthorized',
        message: 'Invalid username or password'
      });
    }

    const token = generateToken(user.userId, user.username, user.role);

    console.log(`[AuthService] User logged in: ${username}`);

    res.status(200).json({
      message: 'Login successful',
      token: token,
      user: {
        userId: user.userId,
        username: user.username,
        role: user.role
      }
    });
  } catch (error) {
    console.error('[AuthService] Login error:', error);
    res.status(500).json({
      error: 'Internal Server Error',
      message: 'Login failed, please try again later'
    });
  }
}

/**
 * JWT token authentication middleware
 */
function authenticateToken(req, res, next) {
  const authHeader = req.headers['authorization'];
  const token = authHeader && authHeader.split(' ')[1]; // Bearer TOKEN

  if (!token) {
    return res.status(401).json({
      error: 'Unauthorized',
      message: 'Missing authentication token'
    });
  }

  const decoded = verifyToken(token);
  if (!decoded) {
    return res.status(403).json({
      error: 'Forbidden',
      message: 'Token is invalid or expired'
    });
  }

  req.user = {
    userId: decoded.userId,
    username: decoded.username,
    role: decoded.role
  };

  next();
}

/**
 * Admin authorization middleware
 */
function requireAdmin(req, res, next) {
  if (req.user && req.user.role === 'admin') {
    next();
  } else {
    res.status(403).json({
      error: 'Forbidden',
      message: 'Admin privileges required'
    });
  }
}

/**
 * Get user ID from JWT token
 */
function getUserId(req) {
  return req.user ? req.user.userId : null;
}

/**
 * Get username from JWT token
 */
function getUsername(req) {
  return req.user ? req.user.username : null;
}

/**
 * Check if user is admin
 */
function isAdmin(req) {
  return req.user && req.user.role === 'admin';
}

/**
 * Get current user info
 */
function getCurrentUser(req, res) {
  try {
    if (!req.user) {
      return res.status(401).json({
        error: 'Unauthorized',
        message: 'Not logged in'
      });
    }

    res.status(200).json({
      user: {
        userId: req.user.userId,
        username: req.user.username,
        role: req.user.role
      }
    });
  } catch (error) {
    console.error('[AuthService] Failed to get user info:', error);
    res.status(500).json({
      error: 'Internal Server Error',
      message: 'Failed to get user info'
    });
  }
}

module.exports = {
  register,
  login,
  authenticateToken,
  requireAdmin,
  getUserId,
  getUsername,
  isAdmin,
  getCurrentUser,
  generateToken,
  verifyToken,
  JWT_SECRET
};
