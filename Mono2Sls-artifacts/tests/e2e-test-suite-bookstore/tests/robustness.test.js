const {
  contract,
  prepareTarget,
  expectStatus,
  getEndpointContract,
  extractList,
  extractObject,
  assertBookShape,
  listBooks,
  getBook,
  searchBooks,
} = require('./helpers');

describe('Robustness', () => {
  beforeAll(async () => {
    await prepareTarget();
  });

  test('ROB-01: catalog responses should return valid JSON with correct Content-Type', async () => {
    const endpoint = getEndpointContract('TC-01');
    const res = await listBooks();

    expectStatus(res, endpoint.successStatus);
    expect(res.headers['content-type']).toMatch(/application\/json/);
    expect(typeof res.data).toBe('object');
  });

  test('ROB-02: should handle concurrent book detail requests', async () => {
    const endpoint = getEndpointContract('TC-03');
    const promises = Array.from({ length: 5 }, () =>
      getBook(contract.fixtures.primaryBookId)
    );

    const results = await Promise.all(promises);
    for (const res of results) {
      expectStatus(res, endpoint.successStatus);
      const book = extractObject(res.data, endpoint);
      expect(book.id).toBe(contract.fixtures.primaryBookId);
    }
  });

  test('ROB-03: list and get should return consistent data for the same book', async () => {
    const listEndpoint = getEndpointContract('TC-01');
    const getEndpoint = getEndpointContract('TC-03');

    const listRes = await listBooks();
    expectStatus(listRes, listEndpoint.successStatus);
    const books = extractList(listRes.data, listEndpoint);
    const fromList = books.find((book) => book.id === contract.fixtures.primaryBookId);
    expect(fromList).toBeDefined();

    const getRes = await getBook(contract.fixtures.primaryBookId);
    expectStatus(getRes, getEndpoint.successStatus);
    const fromGet = extractObject(getRes.data, getEndpoint);

    expect(fromGet.name).toBe(fromList.name);
    expect(fromGet.author).toBe(fromList.author);
    expect(fromGet.price).toBe(fromList.price);
  });

  test('ROB-04: search should handle special characters gracefully', async () => {
    const res = await searchBooks('test<script>alert(1)</script>');
    expect([200, 400]).toContain(res.status);
  });
});
