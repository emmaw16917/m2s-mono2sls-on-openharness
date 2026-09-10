const { authProvider } = require('./setup');
const {
  contract,
  expectStatus,
  createBooking,
  getBooking,
  cancelBooking,
  listCustomerBookings,
  getLoyalty,
  getCustomerId,
} = require('./helpers');

let userA;
let userB;
let bookingId;
let cleanedUp = false;

beforeAll(async () => {
  await authProvider.setup();
  userA = await authProvider.createSession('isolation-a');
  userB = await authProvider.createSession('isolation-b');

  const createRes = await createBooking(userA, { name: 'Isolation booking' });
  expectStatus(createRes, 201);
  bookingId = createRes.data.bookingId || createRes.data.data?.bookingId;
});

afterAll(async () => {
  if (userA && bookingId && !cleanedUp) {
    try {
      await cancelBooking(userA, bookingId);
      cleanedUp = true;
    } catch (_) {
      // Best-effort cleanup only.
    }
  }
  await authProvider.teardown();
});

describe('Isolation Contract', () => {
  test('TC-13a: user B should not fetch user A booking by id', async () => {
    const res = await getBooking(userB, bookingId);
    expectStatus(res, contract.validationRules.forbiddenOrNotFoundStatus);
  });

  test('TC-14a: user B should not list bookings for user A', async () => {
    const res = await listCustomerBookings(userB, getCustomerId(userA));
    expectStatus(res, contract.validationRules.forbiddenOrNotFoundStatus);
  });

  test('TC-15a: user B should not read user A loyalty state', async () => {
    const res = await getLoyalty(userB, getCustomerId(userA));
    expectStatus(res, contract.validationRules.forbiddenOrNotFoundStatus);
  });
});
