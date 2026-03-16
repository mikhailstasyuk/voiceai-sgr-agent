# User Workflows

## Start Conversation
1. Open frontend app.
2. Click mic button.
3. Wait for connected/ready status.
4. Speak into microphone.

Expected result:
- User transcript appears.
- Assistant begins speaking with streamed audio.
- Assistant focuses on appointment-booking behavior and asks clarification questions for missing booking details.

## Book Appointment
1. User asks to book/schedule an appointment.
2. Assistant asks for missing required fields: date, clinic, policy id, and doctor's name.
3. User provides required details.
4. Assistant confirms booking or handles cancellation.

Expected result:
- The turn logic follows the appointment flow state machine.
- Booking confirmation produces a completed appointment flow response.

## Repeated Unclear Intent
1. User provides input that cannot be classified to appointment intent.
2. Assistant asks clarification.
3. This repeats for three consecutive unclear turns.

Expected result:
- Assistant escalates to callback arrangement messaging.

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
