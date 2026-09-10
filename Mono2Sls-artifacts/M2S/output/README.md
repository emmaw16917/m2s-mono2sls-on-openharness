# Serverless Todo Application

This is a production-ready AWS serverless application migrated from a Node.js Express monolith to a fully decoupled architecture using AWS Lambda, API Gateway, DynamoDB, and Cognito.

## Architecture Overview

- **Authentication**: Replaced custom JWT logic with Cognito User Pools. API Gateway enforces authorization via Cognito Authorizer.
- **API Layer**: Each REST endpoint maps to a dedicated Lambda function (6 total).
- **Data Layer**: DynamoDB table `TodoItems` stores items partitioned by `userId` (Cognito Sub UUID) and sorted by `id` (UUID).
- **No Shared Logic**: All functions are independent Workers — no cross-Lambda communication required.
- **No Auth Endpoints**: `/register` and `/login` are removed; frontend uses AWS Amplify or Cognito SDK directly.

## Lambda Functions

| Function Name         | HTTP Method | Path              | Description                                      |
|-----------------------|-------------|-------------------|--------------------------------------------------|
| `todo-list-items`     | GET         | `/item`           | List all todo items for the authenticated user   |
| `todo-get-item`       | GET         | `/item/{id}`      | Get a specific todo item by ID                   |
| `todo-create-item`    | POST        | `/item`           | Create a new todo item                           |
| `todo-update-item`    | PUT         | `/item/{id}`      | Update an existing todo item                     |
| `todo-mark-done`      | POST        | `/item/{id}/done` | Mark a todo item as completed                    |
| `todo-delete-item`    | DELETE      | `/item/{id}`      | Delete a todo item                               |

## Environment Variables

All Lambda functions require these environment variables:

- `TODO_TABLE`: Name of the DynamoDB table (e.g., `TodoItemsTable`)
- `AWS_REGION`: AWS region (e.g., `us-east-1`)

## Dependencies

- `@aws-sdk/lib-dynamodb`: Required for DynamoDB DocumentClient operations.
- `uuid`: Used only in `todo-create-item` and `todo-list-items` for generating item IDs.

## Deployment

This application is designed for deployment via AWS SAM CLI:

```bash
sam build
sam deploy --guided
```

The SAM engineer will generate the necessary `template.yaml` infrastructure file referencing these handlers and environment variables.

## Security & Best Practices

- All user identity is derived from Cognito authorizer claims (`event.requestContext.authorizer.claims.sub`).
- No hardcoded credentials or secrets — all configuration via environment variables.
- Input validation performed on all endpoints (ID format, required fields, JSON parsing).
- Proper error handling with descriptive status codes and messages.
- DynamoDB SDK clients initialized outside the handler for reuse and performance.
- CommonJS modules only — no ESM `import/export` to ensure compatibility with Lambda layers and runtime SDK resolution.