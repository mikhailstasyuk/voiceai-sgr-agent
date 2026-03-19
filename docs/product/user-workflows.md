# User Workflows

## Start Conversation
1. Open frontend app.
2. Click mic button.
3. Wait for connected/ready status.
4. Speak into microphone.

Expected result:
- User transcript appears.
- Assistant begins speaking with streamed audio.
- Assistant begins with greeting plus client qualification question ("Are you our client?") before service routing.

## Book Appointment
1. User asks to book/schedule an appointment.
2. Assistant asks for missing required fields, starting with policy id validation first.
3. If user is unsure about clinic/doctor choice, assistant can proactively list available options.
4. Assistant links clinic/doctor/date through strict schema-guided option selection using current available choices.
5. After selecting a doctor, assistant asks explicit confirmation before proceeding to booking.
6. Assistant constrains doctor choices to available doctors in the selected clinic and only offers currently open slots for that doctor.
7. On invalid or incomplete confirmation data, assistant responds with a targeted correction prompt for the specific missing/invalid field.
8. If user asks for earliest availability, assistant provides earliest doctor/date options across clinics and asks for explicit clinic/doctor choice.
9. User provides required details.
10. Assistant confirms booking or handles cancellation.

Expected result:
- The turn logic follows the appointment flow state machine.
- Clinic choice is based on clinic roster (not filtered by slot availability).
- Date availability is validated after doctor selection and again at booking confirmation.
- Booking confirms only when policy id exists in policyholder data and format is valid, doctor is in roster, and doctor/date slot is available.
- If caller speaks policy prefix and digits across up to 3 recent user turns, assistant assembles `POL-####` and validates it before moving on.
- Assistant accepts spoken policy IDs with or without explicitly saying `dash`, but does not accept bare 4 digits unless recent `POL` prefix context exists.
- Appointment schema selections for clinic/doctor/date use closed option sets with `__NONE__` sentinel (no nullable selection fields in provider contract).
- Duplicate scheduled bookings for the same user/doctor/date are not created.
- Offered availability stays consistent with conflict checks (already-booked slots are not presented as open options).
- If strict schema generation fails on a turn, assistant returns a safe retry/clarification prompt instead of crashing the session.

## Policy Renewal
1. User asks to renew policy.
2. Assistant asks for policy id if missing/invalid.
3. Assistant presents plan options (cheap/intermediate/expensive with monthly price).
4. User confirms renewal choice (same or different plan).
5. Assistant confirms renewal completion.

Expected result:
- Renewal due date is based on 365-day window from last renewal (or policy start date when no previous renewal).
- Policy state is updated with the renewed date and selected plan.
- If user asks which plan they are currently on during renewal, assistant answers using policyholder current plan and continues renewal plan selection.
- If user pivots to another service during renewal, assistant honors that pivot instead of repeating renewal prompts.

## Plan Inquiry
1. User asks about plans or asks to compare plans.
2. Assistant lists plan grid and monthly USD prices.
3. If policy id is available, assistant can compare user current plan against alternatives.
4. If policy id is missing for current-plan comparison, assistant asks for policy id first.
5. Assistant can route user into renewal flow for plan change requests and carry selected target plan.

Expected result:
- Plan information is available for both client and non-client conversations.
- Comparison uses policyholder current plan when known.
- For "change my plan" requests, conversation starts in plan inquiry and then transitions to renewal.
- If user pivots to another service while in plan inquiry, assistant reroutes back to intent selection instead of looping on plan listing.

## Non-Client Onboarding
1. Assistant asks "Are you our client?"
2. User says no.
3. Assistant asks if user wants to become a client and offers:
   - plan info then callback scheduling
   - callback scheduling right away
4. Assistant captures callback phone, then callback date, through callback flow (Georgian mobile format).

Expected result:
- Non-clients can still receive plan information and schedule callback without entering client-only flows.
- If a previously confirmed client later says they are not a client, assistant re-enters this non-client onboarding path.

## Repeated Unclear Intent
1. User provides input that cannot be classified to appointment intent.
2. Assistant asks clarification.
3. This repeats for three consecutive unclear turns.
4. Assistant asks for a callback phone number and validates it.
5. If user asks to return to booking while in callback collection, assistant asks for confirmation and can switch back.

Expected result:
- Assistant requests a Georgian mobile number and normalizes accepted input to `+995#########`.
- Assistant deduplicates repeated `+995` prefix mentions when caller includes country code multiple times.
- Assistant confirms captured number, then captures and confirms callback date, before persisting callback request.
- Callback flow does not trap users who pivot back to booking intent.

## Callback Support
1. User asks callback status or asks why callback was canceled/closed.
2. Assistant routes to callback support handling.
3. Assistant confirms latest callback status or explains there is no active callback.
4. Assistant can offer callback rescheduling and collect phone when requested.

Expected result:
- Callback support requests do not get routed into appointment booking policy-id prompts.
- Users can reschedule callback directly from callback support turns.
- If user pivots from callback-support to another service request, assistant reroutes through intent detection in the same turn.

## Policy Unavailable Recovery
1. User enters a client-only flow and repeatedly says they do not have a policy id.
2. Assistant detects policy-unavailable signal.
3. Assistant hands off to non-client options (plan information + callback choices).

Expected result:
- Assistant avoids repeated strict `POL-####` dead-end loops.
- Conversation moves to viable non-client path without manual reset.

## Interrupt Assistant (Barge-In)
1. While assistant is speaking, user starts speaking again.

Expected result:
- Ongoing assistant output is interrupted quickly.
- New user utterance becomes active turn input.

## Stop Conversation
1. Click mic button again (stop).

Expected result:
- Session closes cleanly.
- UI returns to idle state.

## Error Recovery
1. If status enters error, user restarts session.

Expected result:
- New clean session can be started without page reload in normal cases.
