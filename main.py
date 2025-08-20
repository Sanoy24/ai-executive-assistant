import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import base64
from email.mime.text import MIMEText

import google.generativeai as genai
from fastapi import FastAPI, BackgroundTasks, HTTPException
from google import genai
from dotenv import dotenv_values
from google.genai import types
from google.cloud import firestore
from googleapiclient.discovery import build
import sendgrid
from sendgrid.helpers.mail import Mail, To, Content, Email
import uvicorn


app = FastAPI(title="AI Executive Assistant", version="1.0.0")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

env_values = dotenv_values(".env")

client = genai.Client(api_key=env_values.get("GEMINI_API_KEY"))


db = firestore.Client()
sg = sendgrid.SendGridAPIClient(api_key=env_values.get("SENDGRID_API_KEY"))


class AIExecutiveAssistant:
    def __init__(self):
        self.gmail_service = None
        self.calendar_service = None
        self.credentials = None
        self.initialize_google_services()

    def initialize_google_services(self):
        """initialize google api services"""
        try:
            creds_json = env_values("GOOGLE_CREDENTIALS_JSON")
            if creds_json:
                import json
                from google.oauth2 import service_account

                creds_data = json.loads(creds_json)
                self.credentials = (
                    service_account.Credentials.from_service_account_info(
                        creds_data,
                        scopes=[
                            "https://www.googleapis.com/auth/gmail.readonly",
                            "https://www.googleapis.com/auth/gmail.send",
                            "https://www.googleapis.com/auth/calendar",
                        ],
                    )
                )
            else:
                return
            self.gmail_service = build("gmail", "v1", credentials=self.credentials)
            self.calendar_service = build(
                "calendar", "v3", credentials=self.credentials
            )
            logger.info("Google services initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google services: {str(e)}")
            raise

    async def process_meeting_request(self, email_data: Dict) -> Dict:
        """process incoming meeting requests"""

        try:
            meeting_info = self.extract_meeting_info(email_data["body"])
            if not meeting_info or not meeting_info.get("is_meeting_request"):
                return {"status": "not a meeting request"}
            # Find available time slots
            available_slots = await self.find_available_times(
                duration_minutes=meeting_info.get("duration", 30),
                preferred_times=meeting_info.get("preferred_times", []),
                urgency=meeting_info.get("urgency", "medium"),
            )

            if not available_slots:
                return {
                    "status": "no_availability",
                    "message": "No suitable time slots found",
                }

            event_result = await self.create_calendar_event(
                meeting_info, available_slots[0], email_data["sender"]
            )

            if event_result["success"]:
                # send confirmation email
                await self.send_meeting_conformation(
                    email_data["sender"], meeting_info, event_result["event"]
                )

                # Log activity
                await self.log_activity(
                    {
                        "type": "meeting_scheduled",
                        "attendee": email_data["sender"],
                        "subject": meeting_info.get("subject", "Meeting"),
                        "datetime": available_slots[0],
                        "status": "confirmed",
                    }
                )

                return {
                    "status": "scheduled",
                    "event_id": event_result["event"]["id"],
                    "datetime": available_slots[0],
                }
        except Exception as e:
            logger.error(f"Error processing meeting request: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def extract_meeting_info(self, email_body: str) -> Dict:
        """Use AI to extract meeting information from email"""
        try:
            extraction_prompt = f"""
                Analyze this email and extract meeting information. Return JSON only.

                Email content: {email_body}

                Extract:
                {{
                    "is_meeting_request": true/false,
                    "subject": "meeting topic",
                    "duration": estimated_minutes (default 30),
                    "urgency": "high/medium/low",
                    "preferred_times": ["Monday morning", "next week", etc],
                    "attendees": ["email@domain.com"],
                    "meeting_type": "in-person/virtual/phone",
                    "location": "if mentioned",
                    "agenda_items": ["item1", "item2"]
                }}

                If not a meeting request, return {{"is_meeting_request": false}}
                """

            response = await client.models.generate_content(
                model="gemini-2.0-flash-001", contents=extraction_prompt
            )

            # Clean and parse JSON response
            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:-3]
            elif response_text.startswith("```"):
                response_text = response_text[3:-3]

            try:
                meeting_info = json.loads(response_text)
                return meeting_info
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse AI response as JSON: {response_text}")
                return {"is_meeting_request": False}

        except Exception as e:
            logger.error(f"Error extracting meeting info: {str(e)}")
            return {"is_meeting_request": False}

    async def find_available_times(
        self,
        duration_minutes: int = 30,
        preferred_times: List[str] = None,
        urgency: str = "medium",
    ) -> List[str]:
        """Find available time slots in calendar"""
        try:
            if not self.calendar_service:
                logger.warning("Calendar service not initialized")
                return []

            # Define search window based on urgency
            if urgency == "high":
                days_ahead = 3
            elif urgency == "medium":
                days_ahead = 7
            else:
                days_ahead = 14

            time_min = datetime.utcnow().isoformat() + "Z"
            time_max = (
                datetime.utcnow() + timedelta(days=days_ahead)
            ).isoformat() + "Z"

            # Get busy times
            freebusy_result = (
                self.calendar_service.freebusy()
                .query(
                    body={
                        "timeMin": time_min,
                        "timeMax": time_max,
                        "items": [{"id": "primary"}],
                    }
                )
                .execute()
            )

            busy_times = freebusy_result["calendars"]["primary"]["busy"]

            # Find free slots
            available_slots = []
            current_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

            # Only consider business hours (9 AM - 5 PM, weekdays)
            while current_time < datetime.utcnow() + timedelta(days=days_ahead):
                if current_time.weekday() < 5 and 9 <= current_time.hour < 17:
                    slot_end = current_time + timedelta(minutes=duration_minutes)

                    # Check if slot conflicts with busy times
                    is_free = True
                    for busy_period in busy_times:
                        busy_start = datetime.fromisoformat(
                            busy_period["start"].replace("Z", "+00:00")
                        )
                        busy_end = datetime.fromisoformat(
                            busy_period["end"].replace("Z", "+00:00")
                        )

                        if not (slot_end <= busy_start or current_time >= busy_end):
                            is_free = False
                            break

                    if is_free:
                        available_slots.append(current_time.isoformat())
                        if len(available_slots) >= 5:  # Return top 5 options
                            break

                current_time += timedelta(minutes=30)  # Check every 30 minutes

            return available_slots

        except Exception as e:
            logger.error(f"Error finding available times: {str(e)}")
            return []

    async def create_calendar_event(
        self, meeting_info: Dict, datetime_slot: str, attendee_email: str
    ) -> Dict:
        """Create calendar event"""
        try:
            if not self.calendar_service:
                return {"success": False, "error": "Calendar service not available"}

            start_time = datetime.fromisoformat(datetime_slot)
            end_time = start_time + timedelta(minutes=meeting_info.get("duration", 30))

            event = {
                "summary": meeting_info.get("subject", "Meeting"),
                "description": f"Automatically scheduled meeting\n\nAgenda:\n"
                + "\n".join(meeting_info.get("agenda_items", ["Discussion"])),
                "start": {
                    "dateTime": start_time.isoformat(),
                    "timeZone": "America/New_York",
                },
                "end": {
                    "dateTime": end_time.isoformat(),
                    "timeZone": "America/New_York",
                },
                "attendees": [{"email": attendee_email}],
                "conferenceData": (
                    {
                        "createRequest": {
                            "requestId": f"meet-{int(start_time.timestamp())}",
                            "conferenceSolutionKey": {"type": "hangoutsMeet"},
                        }
                    }
                    if meeting_info.get("meeting_type") == "virtual"
                    else None
                ),
            }

            created_event = (
                self.calendar_service.events()
                .insert(
                    calendarId="primary",
                    body=event,
                    conferenceDataVersion=(
                        1 if meeting_info.get("meeting_type") == "virtual" else 0
                    ),
                    sendUpdates="all",
                )
                .execute()
            )

            return {"success": True, "event": created_event}

        except Exception as e:
            logger.error(f"Error creating calendar event: {str(e)}")
            return {"success": False, "error": str(e)}

    async def send_meeting_confirmation(
        self, attendee_email: str, meeting_info: Dict, calendar_event: Dict
    ):
        """Send meeting confirmation email"""
        try:
            start_time = datetime.fromisoformat(
                calendar_event["start"]["dateTime"].replace("Z", "+00:00")
            )

            confirmation_prompt = f"""
            Generate a professional meeting confirmation email.
            
            Details:
            - Meeting: {meeting_info.get('subject', 'Meeting')}
            - Date/Time: {start_time.strftime('%A, %B %d at %I:%M %p')}
            - Duration: {meeting_info.get('duration', 30)} minutes
            - Meeting link: {calendar_event.get('hangoutLink', 'Details in calendar invite')}
            
            Email should:
            - Confirm the meeting is scheduled
            - Include date/time and meeting details
            - Be professional but friendly
            - Under 150 words
            """

            response = await client.models.generate_content(
                model="gemini-2.0-flash-001", contents=confirmation_prompt
            )
            email_body = response.text.strip()

            # Send via SendGrid
            from_email = Email(
                os.getenv("FROM_EMAIL", "assistant@yourcompany.com"),
                "AI Executive Assistant",
            )
            to_email = To(attendee_email)
            subject = f"Meeting Confirmed: {meeting_info.get('subject', 'Meeting')} - {start_time.strftime('%b %d')}"
            content = Content("text/plain", email_body)

            mail = Mail(from_email, to_email, subject, content)
            response = sg.send(mail)

            if response.status_code == 202:
                logger.info(f"Confirmation email sent to {attendee_email}")
                return True
            else:
                logger.error(
                    f"Failed to send confirmation email: {response.status_code}"
                )
                return False

        except Exception as e:
            logger.error(f"Error sending confirmation email: {str(e)}")
            return False

    async def process_incoming_emails(self) -> Dict:
        """Process new emails for meeting requests and other actions"""
        try:
            if not self.gmail_service:
                return {"error": "Gmail service not initialized"}

            # Get unread emails from last 24 hours
            query = "is:unread newer_than:1d"
            result = (
                self.gmail_service.users()
                .messages()
                .list(userId="me", q=query, maxResults=50)
                .execute()
            )

            messages = result.get("messages", [])
            processed_count = 0
            meeting_requests = 0

            for message in messages:
                try:
                    # Get full message
                    msg = (
                        self.gmail_service.users()
                        .messages()
                        .get(userId="me", id=message["id"])
                        .execute()
                    )

                    # Extract email data
                    email_data = await self.parse_email_message(msg)

                    if email_data:
                        # Classify email
                        classification = await self.classify_email(email_data["body"])

                        # Process based on classification
                        if classification.get("type") == "meeting_request":
                            result = await self.process_meeting_request(email_data)
                            if result.get("status") == "scheduled":
                                meeting_requests += 1

                        elif classification.get("priority") == "high":
                            await self.handle_urgent_email(email_data, classification)

                        elif classification.get("can_auto_respond"):
                            await self.send_auto_response(email_data, classification)

                        # Mark as processed
                        self.gmail_service.users().messages().modify(
                            userId="me",
                            id=message["id"],
                            body={"removeLabelIds": ["UNREAD"]},
                        ).execute()

                        processed_count += 1

                except Exception as e:
                    logger.error(f"Error processing message {message['id']}: {str(e)}")
                    continue

            return {
                "processed_emails": processed_count,
                "meeting_requests_handled": meeting_requests,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error processing emails: {str(e)}")
            return {"error": str(e)}

    async def parse_email_message(self, message: Dict) -> Optional[Dict]:
        """Parse Gmail message into structured data"""
        try:
            headers = message["payload"].get("headers", [])

            # Extract headers
            sender = next((h["value"] for h in headers if h["name"] == "From"), "")
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
            date = next((h["value"] for h in headers if h["name"] == "Date"), "")

            # Extract body
            body = ""
            if "parts" in message["payload"]:
                for part in message["payload"]["parts"]:
                    if part["mimeType"] == "text/plain":
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode(
                            "utf-8"
                        )
                        break
            elif message["payload"]["body"].get("data"):
                body = base64.urlsafe_b64decode(
                    message["payload"]["body"]["data"]
                ).decode("utf-8")

            return {
                "id": message["id"],
                "sender": sender,
                "subject": subject,
                "body": body,
                "date": date,
            }

        except Exception as e:
            logger.error(f"Error parsing email message: {str(e)}")
            return None

    async def classify_email(self, email_body: str) -> Dict:
        """Classify email for appropriate action"""
        try:
            classification_prompt = f"""
            Classify this email and determine appropriate actions. Return JSON only.

            Email: {email_body}

            Classify:
            {{
                "type": "meeting_request/question/update/spam/newsletter/urgent_request",
                "priority": "high/medium/low",
                "sentiment": "positive/neutral/negative", 
                "can_auto_respond": true/false,
                "action_items": ["item1", "item2"],
                "response_urgency": "immediate/within_day/within_week",
                "key_topics": ["topic1", "topic2"]
            }}
            """

            response = await client.models.generate_content(
                model="gemini-2.0-flash-001", contents=classification_prompt
            )

            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:-3]
            elif response_text.startswith("```"):
                response_text = response_text[3:-3]

            try:
                classification = json.loads(response_text)
                return classification
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse classification JSON: {response_text}")
                return {
                    "type": "unknown",
                    "priority": "medium",
                    "can_auto_respond": False,
                }

        except Exception as e:
            logger.error(f"Error classifying email: {str(e)}")
            return {"type": "unknown", "priority": "medium", "can_auto_respond": False}

    async def send_auto_response(self, email_data: Dict, classification: Dict):
        """Generate and send automatic response"""
        try:
            response_prompt = f"""
            Generate a professional auto-response email.
            
            Original email: {email_data['body']}
            Classification: {classification}
            
            The response should:
            - Acknowledge their message
            - Provide helpful information or next steps
            - Be professional and courteous
            - Under 100 words
            - If it's a question, provide a helpful answer or indicate when they'll get a full response
            """

            response = await client.models.generate_content(
                model="gemini-2.0-flash-001", contents=response_prompt
            )
            response_body = response.text.strip()

            # Send response via SendGrid
            from_email = Email(
                os.getenv("FROM_EMAIL", "assistant@yourcompany.com"),
                "AI Executive Assistant",
            )

            # Extract sender email from "Name <email@domain.com>" format
            sender_match = re.search(r"<([^>]+)>", email_data["sender"])
            sender_email = (
                sender_match.group(1) if sender_match else email_data["sender"]
            )

            to_email = To(sender_email)
            subject = f"Re: {email_data['subject']}"
            content = Content("text/plain", response_body)

            mail = Mail(from_email, to_email, subject, content)
            response = sg.send(mail)

            if response.status_code == 202:
                await self.log_activity(
                    {
                        "type": "auto_response",
                        "recipient": sender_email,
                        "subject": subject,
                        "classification": classification,
                        "status": "sent",
                    }
                )
                logger.info(f"Auto-response sent to {sender_email}")
                return True

        except Exception as e:
            logger.error(f"Error sending auto-response: {str(e)}")
            return False

    async def handle_urgent_email(self, email_data: Dict, classification: Dict):
        """Handle urgent emails with immediate notification"""
        try:
            # Log urgent email
            await self.log_activity(
                {
                    "type": "urgent_email",
                    "sender": email_data["sender"],
                    "subject": email_data["subject"],
                    "classification": classification,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

            # In a real implementation, this would send Slack notification
            # or SMS to the executive
            logger.info(
                f"URGENT EMAIL from {email_data['sender']}: {email_data['subject']}"
            )

        except Exception as e:
            logger.error(f"Error handling urgent email: {str(e)}")

    async def daily_summary(self) -> Dict:
        """Generate daily activity summary"""
        try:
            today = datetime.utcnow().date()

            # Get today's activities
            activities_ref = db.collection("activities")
            today_start = datetime.combine(today, datetime.min.time())
            today_end = datetime.combine(today, datetime.max.time())

            activities = []
            for doc in (
                activities_ref.where("timestamp", ">=", today_start)
                .where("timestamp", "<=", today_end)
                .stream()
            ):
                activities.append(doc.to_dict())

            # Generate summary with AI
            summary_prompt = f"""
            Generate a brief daily summary for an executive based on these activities:
            
            Activities: {json.dumps(activities, default=str, indent=2)}
            
            Create a summary including:
            - Meetings scheduled: count and key topics
            - Emails processed: count and priority breakdown
            - Action items created
            - Overall productivity insights
            
            Keep it concise but informative (under 200 words).
            """

            response = client.models.generate_content(
                model="gemini-2.0-flash-001", contents=summary_prompt
            )
            summary_text = response.text.strip()

            return {
                "date": today.isoformat(),
                "total_activities": len(activities),
                "summary": summary_text,
                "raw_activities": activities,
            }

        except Exception as e:
            logger.error(f"Error generating daily summary: {str(e)}")
            return {"error": str(e)}

    async def log_activity(self, activity_data: Dict):
        """Log activity to Firestore"""
        try:
            activity_data["timestamp"] = datetime.utcnow()
            db.collection("activities").add(activity_data)
        except Exception as e:
            logger.error(f"Error logging activity: {str(e)}")


# Initialize the AI Executive Assistant
ai_assistant = AIExecutiveAssistant()


# FastAPI Endpoints
@app.get("/")
async def root():
    return {
        "message": "AI Executive Assistant is running",
        "version": "1.0.0",
        "services": {
            "gmail": ai_assistant.gmail_service is not None,
            "calendar": ai_assistant.calendar_service is not None,
        },
    }


@app.post("/process-emails")
async def process_emails(background_tasks: BackgroundTasks):
    """Manually trigger email processing"""
    background_tasks.add_task(ai_assistant.process_incoming_emails)
    return {"message": "Email processing triggered"}


@app.post("/schedule-meeting")
async def schedule_meeting(meeting_request: Dict):
    """Manually schedule a meeting"""
    try:
        result = await ai_assistant.process_meeting_request(meeting_request)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/daily-summary")
async def get_daily_summary():
    """Get daily activity summary"""
    try:
        summary = await ai_assistant.daily_summary()
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/activities")
async def get_activities(limit: int = 20):
    """Get recent activities"""
    try:
        activities_ref = (
            db.collection("activities")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        activities = [doc.to_dict() for doc in activities_ref.stream()]
        return {"activities": activities, "count": len(activities)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/calendar/availability")
async def check_availability(duration: int = 30, days_ahead: int = 7):
    """Check calendar availability"""
    try:
        available_slots = await ai_assistant.find_available_times(
            duration_minutes=duration, urgency="medium"
        )
        return {"available_slots": available_slots[:10]}  # Return top 10
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Cloud Function entry points for scheduled tasks
def scheduled_email_processing(request):
    """Cloud Function for scheduled email processing"""

    async def run():
        result = await ai_assistant.process_incoming_emails()
        return result

    result = asyncio.run(run())
    return {"result": result, "status": "completed"}


def scheduled_daily_summary(request):
    """Cloud Function for daily summary generation"""

    async def run():
        summary = await ai_assistant.daily_summary()

        # In production, this would email the summary to the executive
        logger.info(f"Daily summary generated: {summary}")
        return summary

    result = asyncio.run(run())
    return {"result": result, "status": "completed"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
