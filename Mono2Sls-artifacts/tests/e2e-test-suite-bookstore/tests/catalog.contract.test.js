const {
  contract,
  prepareTarget,
  expectStatus,
  getEndpointContract,
  extractObject,
  extractList,
  assertBookShape,
  listBooks,
  getBook,
  searchBooks,
} = require('./helpers');

describe('Catalog Contract', () => {
  beforeAll(async () => {
    await prepareTarget();
  });

  test('TC-01: should list seeded books', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await listBooks();

    expectStatus(res, endpoint.successStatus);
    const books = extractList(res.data, endpoint);
    expect(books.length).toBeGreaterThanOrEqual(5);
    expect(books.some((book) => book.id === contract.fixtures.primaryBookId)).toBe(true);
    assertBookShape(
      books.find((book) => book.id === contract.fixtures.primaryBookId),
      endpoint.response.requiredBookFields
    );
  });

  test('TC-02: should filter books by category', async () => {
    const endpoint = getEndpointContract('TC-02');
    const res = await listBooks({ category: contract.fixtures.category });

    expectStatus(res, endpoint.successStatus);
    const books = extractList(res.data, endpoint);
    expect(books.length).toBeGreaterThanOrEqual(1);
    expect(books.every((book) => book.category === contract.fixtures.category)).toBe(true);
  });

  test('TC-03: should fetch a known book by id', async () => {
    const endpoint = getEndpointContract('TC-03');
    const res = await getBook(contract.fixtures.primaryBookId);

    expectStatus(res, endpoint.successStatus);
    const book = extractObject(res.data, endpoint);
    assertBookShape(book, endpoint.response.requiredBookFields);
    expect(book.id).toBe(contract.fixtures.primaryBookId);
  });

  test('TC-04: should reject unknown book ids', async () => {
    const endpoint = getEndpointContract('TC-03');
    const res = await getBook(contract.fixtures.invalidBookId);
    expectStatus(res, endpoint.notFoundStatus);
  });

  test('TC-05: should search books by keyword', async () => {
    const endpoint = getEndpointContract('TC-05');
    const res = await searchBooks(contract.fixtures.searchKeyword);

    expectStatus(res, endpoint.successStatus);
    const payload = extractObject(res.data, endpoint);
    expect(typeof payload.total).toBe('number');
    expect(Array.isArray(payload.hits)).toBe(true);
    expect(payload.total).toBeGreaterThanOrEqual(1);
    expect(payload.hits.some((hit) => hit && hit._source && hit._source.id === contract.fixtures.primaryBookId)).toBe(true);
  });

  test('TC-06: should reject search without q', async () => {
    const res = await searchBooks();
    expectStatus(res, contract.validationRules.badRequestStatus);
  });
});
