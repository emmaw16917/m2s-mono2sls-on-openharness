// Usage: node req_time.js <file.js>  -- prints require() ms for one fresh process (cold-start proxy).
// Compare h_<name>.js (unbundled) vs b_<name>.js (npx esbuild h_<name>.js --bundle --platform=node --target=node22 --minify --format=cjs --outfile=b_<name>.js).
process.env.AWS_REGION='us-east-1';process.env.BESTSELLER_QUEUE_URL='x';for(const k of ['BESTSELLERS_TABLE','BOOKS_TABLE','CART_TABLE','ORDERS_TABLE'])process.env[k]='t';
const t0=process.hrtime.bigint(); require('./'+process.argv[2]); console.log((Number(process.hrtime.bigint()-t0)/1e6).toFixed(2));
