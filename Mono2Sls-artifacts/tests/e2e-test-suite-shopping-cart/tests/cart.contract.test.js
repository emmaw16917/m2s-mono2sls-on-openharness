const {
  authProvider,
  prepareTarget,
  getRuntimeState,
  expectStatus,
  getEndpointContract,
  extractList,
  createAnonymousSession,
  createSession,
  copyCookies,
  findCartItem,
  getCart,
  addToCart,
  updateCart,
  migrateCart,
  checkoutCart,
  getCartTotal,
  pollUntil,
} = require('./helpers');

describe('Cart Contract', () => {
  let runtimeState;

  beforeAll(async () => {
    await prepareTarget();
    runtimeState = getRuntimeState();
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-04: should initialize an anonymous cart and set cartId cookie', async () => {
    const endpoint = getEndpointContract('TC-04');
    const anonymous = createAnonymousSession('tc04');

    const res = await getCart(anonymous);

    expectStatus(res, endpoint.successStatus);
    const products = extractList(res.data, endpoint);
    expect(products).toHaveLength(0);
    expect(typeof anonymous.cookieJar.cartId).toBe('string');
    expect(anonymous.cookieJar.cartId.length).toBeGreaterThan(0);
  });

  test('TC-05: should add an item to anonymous cart and increase total quantity', async () => {
    const endpoint = getEndpointContract('TC-05');
    const anonymous = createAnonymousSession('tc05');
    const productId = runtimeState.fixtures.primaryProductId;

    await getCart(anonymous);
    const addRes = await addToCart(anonymous, productId, 1);
    expectStatus(addRes, endpoint.successStatus);

    const cartRes = await pollUntil(
      () => getCart(anonymous),
      (res) => {
        if (res.status !== 200) {
          return false;
        }
        const products = extractList(res.data, getEndpointContract('TC-04'));
        const item = findCartItem(products, productId);
        return item && item.quantity === 1;
      }
    );

    const products = extractList(cartRes.data, getEndpointContract('TC-04'));
    const item = findCartItem(products, productId);
    expect(item).toBeDefined();
    expect(item.quantity).toBe(1);

    const totalRes = await pollUntil(
      () => getCartTotal(productId),
      (res) => res.status === 200 && res.data && res.data.quantity === 1
    );
    expectStatus(totalRes, getEndpointContract('TC-10').successStatus);
    expect(totalRes.data.product).toBe(productId);
    expect(totalRes.data.quantity).toBe(1);
  });

  test('TC-06 / TC-07: should update quantity and delete item when quantity becomes zero', async () => {
    const endpoint = getEndpointContract('TC-06');
    const anonymous = createAnonymousSession('tc06');
    const productId = runtimeState.fixtures.secondaryProductId;

    await getCart(anonymous);
    await addToCart(anonymous, productId, 1);

    const updateRes = await updateCart(anonymous, productId, 3);
    expectStatus(updateRes, endpoint.successStatus);

    const updatedTotal = await pollUntil(
      () => getCartTotal(productId),
      (res) => res.status === 200 && res.data && res.data.quantity === 3
    );
    expect(updatedTotal.data.quantity).toBe(3);

    const zeroRes = await updateCart(anonymous, productId, 0);
    expectStatus(zeroRes, endpoint.successStatus);

    const finalCartRes = await pollUntil(
      () => getCart(anonymous),
      (res) => {
        if (res.status !== 200) {
          return false;
        }
        const products = extractList(res.data, getEndpointContract('TC-04'));
        return !findCartItem(products, productId);
      }
    );

    const finalProducts = extractList(finalCartRes.data, getEndpointContract('TC-04'));
    expect(findCartItem(finalProducts, productId)).toBeUndefined();

    const finalTotalRes = await pollUntil(
      () => getCartTotal(productId),
      (res) => res.status === 200 && res.data && res.data.quantity === 0
    );
    expect(finalTotalRes.data.quantity).toBe(0);
  });

  test('TC-08: should migrate anonymous cart into authenticated user cart', async () => {
    const endpoint = getEndpointContract('TC-08');
    const anonymous = createAnonymousSession('tc08-anon');
    const productId = runtimeState.fixtures.tertiaryProductId;

    await getCart(anonymous);
    await addToCart(anonymous, productId, 2);

    const authenticated = await createSession('tc08-user');
    copyCookies(anonymous, authenticated);

    const migrateRes = await migrateCart(authenticated);
    expectStatus(migrateRes, endpoint.successStatus);

    const migratedProducts = extractList(migrateRes.data, endpoint);
    const migratedItem = findCartItem(migratedProducts, productId);
    expect(migratedItem).toBeDefined();
    expect(migratedItem.quantity).toBe(2);

    const authedCartRes = await pollUntil(
      () => getCart(authenticated),
      (res) => {
        if (res.status !== 200) {
          return false;
        }
        const products = extractList(res.data, getEndpointContract('TC-04'));
        const item = findCartItem(products, productId);
        return item && item.quantity === 2;
      }
    );
    const authedProducts = extractList(authedCartRes.data, getEndpointContract('TC-04'));
    expect(findCartItem(authedProducts, productId).quantity).toBe(2);
  });

  test('TC-09: should checkout authenticated cart and clear quantities', async () => {
    const endpoint = getEndpointContract('TC-09');
    const authenticated = await createSession('tc09-user');
    const productId = runtimeState.fixtures.quaternaryProductId;

    await addToCart(authenticated, productId, 2);

    const checkoutRes = await checkoutCart(authenticated);
    expectStatus(checkoutRes, endpoint.successStatus);

    const checkedOutProducts = extractList(checkoutRes.data, endpoint);
    const checkedOutItem = findCartItem(checkedOutProducts, productId);
    expect(checkedOutItem).toBeDefined();
    expect(checkedOutItem.quantity).toBe(2);

    const finalCartRes = await pollUntil(
      () => getCart(authenticated),
      (res) => {
        if (res.status !== 200) {
          return false;
        }
        const products = extractList(res.data, getEndpointContract('TC-04'));
        return !findCartItem(products, productId);
      }
    );
    const finalProducts = extractList(finalCartRes.data, getEndpointContract('TC-04'));
    expect(findCartItem(finalProducts, productId)).toBeUndefined();

    const totalRes = await pollUntil(
      () => getCartTotal(productId),
      (res) => res.status === 200 && res.data && res.data.quantity === 0
    );
    expect(totalRes.data.quantity).toBe(0);
  });

  test('TC-11: migration should preserve total quantity instead of double counting', async () => {
    const anonymous = createAnonymousSession('tc11-anon');
    const sourceProductId = runtimeState.fixtures.primaryProductId;
    const quantity = 4;

    await getCart(anonymous);
    await addToCart(anonymous, sourceProductId, quantity);

    const beforeTotal = await pollUntil(
      () => getCartTotal(sourceProductId),
      (res) => res.status === 200 && res.data && res.data.quantity === quantity
    );
    expect(beforeTotal.data.quantity).toBe(quantity);

    const authenticated = await createSession('tc11-user');
    copyCookies(anonymous, authenticated);

    const migrateRes = await migrateCart(authenticated);
    expectStatus(migrateRes, getEndpointContract('TC-08').successStatus);

    const afterTotal = await pollUntil(
      () => getCartTotal(sourceProductId),
      (res) => res.status === 200 && res.data && res.data.quantity === quantity
    );
    expect(afterTotal.data.quantity).toBe(quantity);
  });
});
