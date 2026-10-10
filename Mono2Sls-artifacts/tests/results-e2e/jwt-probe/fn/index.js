exports.handler = async (event) => ({
  statusCode: 200,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ requestContext: event.requestContext, version: event.version || null,
                         headers: event.headers, path: event.path || event.rawPath }),
});
