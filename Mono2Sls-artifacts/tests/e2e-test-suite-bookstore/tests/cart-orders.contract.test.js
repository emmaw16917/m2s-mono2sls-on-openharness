const {
  contract,
  authProvider,
  prepareTarget,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  createSession,
  getCart,
  getCartItem,
  addCartItem,
  updateCartItem,
  deleteCartItem,
  createOrder,
  listOrders,
} = require('./helpers');

describe('Cart And Orders Contract', () => {
  let session;
  let orderId;

  beforeAll(async () => {
    await prepareTarget();
    session = await createSession('bookstore-contract');
  });

  afterAll(async () => {
    await authProvider.teardown();
  });

  test('TC-07: should support cart CRUD flow', async () => {
    const addEndpoint = getEndpointContract('TC-07C');
    const getEndpoint = getEndpointContract('TC-07');
    const getItemEndpoint = getEndpointContract('TC-07B');
    const updateEndpoint = getEndpointContract('TC-07D');
    const deleteEndpoint = getEndpointContract('TC-07E');

    const addRes = await addCartItem(session, {
      bookId: contract.fixtures.primaryBookId,
      quantity: 1,
      price: 99,
    });
    expectStatus(addRes, addEndpoint.successStatus);

    const listAfterAdd = await getCart(session);
    expectStatus(listAfterAdd, getEndpoint.successStatus);
    const cartItems = extractList(listAfterAdd.data, getEndpoint);
    expect(cartItems.some((item) => item.bookId === contract.fixtures.primaryBookId)).toBe(true);

    const updateRes = await updateCartItem(session, {
      bookId: contract.fixtures.primaryBookId,
      quantity: 3,
    });
    expectStatus(updateRes, updateEndpoint.successStatus);

    const getItemRes = await getCartItem(session, contract.fixtures.primaryBookId);
    expectStatus(getItemRes, getItemEndpoint.successStatus);
    const cartItem = extractObject(getItemRes.data, getItemEndpoint);
    expect(cartItem.bookId).toBe(contract.fixtures.primaryBookId);
    expect(cartItem.quantity).toBe(3);

    const deleteRes = await deleteCartItem(session, contract.fixtures.primaryBookId);
    expectStatus(deleteRes, deleteEndpoint.successStatus);

    const listAfterDelete = await getCart(session);
    expectStatus(listAfterDelete, getEndpoint.successStatus);
    const finalCart = extractList(listAfterDelete.data, getEndpoint);
    expect(finalCart.some((item) => item.bookId === contract.fixtures.primaryBookId)).toBe(false);
  });

  test('TC-08: should create an order from cart items', async () => {
    const endpoint = getEndpointContract('TC-08');

    await addCartItem(session, {
      bookId: contract.fixtures.primaryBookId,
      quantity: 1,
      price: 99,
    });
    await addCartItem(session, {
      bookId: contract.fixtures.secondaryBookId,
      quantity: 2,
      price: 89,
    });

    const res = await createOrder(session, [
      { bookId: contract.fixtures.primaryBookId, quantity: 1, price: 99 },
      { bookId: contract.fixtures.secondaryBookId, quantity: 2, price: 89 },
    ]);

    expectStatus(res, endpoint.successStatus);
    const order = extractObject(res.data, endpoint);
    expect(typeof order.orderId).toBe('string');
    orderId = order.orderId;
  });

  test('TC-09: should expose order history and clear purchased cart items', async () => {
    const endpoint = getEndpointContract('TC-09');
    const cartEndpoint = getEndpointContract('TC-07');

    const ordersRes = await listOrders(session);
    expectStatus(ordersRes, endpoint.successStatus);
    const orders = extractList(ordersRes.data, endpoint);
    const createdOrder = orders.find((item) => item.orderId === orderId);
    expect(createdOrder).toBeDefined();
    expect(Array.isArray(createdOrder.books)).toBe(true);
    expect(createdOrder.books.some((book) => book.bookId === contract.fixtures.primaryBookId)).toBe(true);

    const cartRes = await getCart(session);
    expectStatus(cartRes, cartEndpoint.successStatus);
    const cartItems = extractList(cartRes.data, cartEndpoint);
    expect(cartItems.some((item) => item.bookId === contract.fixtures.primaryBookId)).toBe(false);
    expect(cartItems.some((item) => item.bookId === contract.fixtures.secondaryBookId)).toBe(false);
  });
});
