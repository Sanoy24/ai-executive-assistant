# setup_cloud.py - Google Cloud Infrastructure Setup
import os
from google.cloud import scheduler
from google.cloud import firestore


def setup_firestore():
    """Initialize Firestore database with collections"""
    try:
        db = firestore.Client()

        # Create initial collections with sample documents
        collections = ["activities", "meetings", "email_templates", "user_preferences"]

        for collection_name in collections:
            # Add a sample document to create the collection
            sample_doc = {
                "created_at": firestore.SERVER_TIMESTAMP,
                "type": "initialization",
                "collection": collection_name,
            }
            db.collection(collection_name).add(sample_doc)
            print(f"✅ Created collection: {collection_name}")

        print("✅ Firestore setup complete")
        return True

    except Exception as e:
        print(f"❌ Error setting up Firestore: {e}")
        return False


def setup_cloud_scheduler():
    """Set up Cloud Scheduler jobs for automated tasks"""
    try:
        client = scheduler.CloudSchedulerClient()
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = "us-central1"
        parent = f"projects/{project_id}/locations/{location}"

        # Cloud Run service URL
        service_url = f"https://ai-executive-assistant-{project_id}.uc.r.appspot.com"

        jobs = [
            {
                "name": f"{parent}/jobs/process-emails",
                "description": "Process incoming emails every 30 minutes during business hours",
                "schedule": "*/30 9-17 * * 1-5",  # Every 30 min, 9 AM-5 PM, weekdays
                "time_zone": "America/New_York",
                "http_target": {
                    "uri": f"{service_url}/process-emails",
                    "http_method": "POST",
                    "headers": {"Content-Type": "application/json"},
                },
                "retry_config": {
                    "retry_count": 3,
                    "max_retry_duration": "300s",
                    "min_backoff_duration": "5s",
                },
            },
            {
                "name": f"{parent}/jobs/daily-summary",
                "description": "Generate daily summary report at 6 PM",
                "schedule": "0 18 * * 1-5",  # 6 PM, weekdays
                "time_zone": "America/New_York",
                "http_target": {
                    "uri": f"{service_url}/daily-summary",
                    "http_method": "GET",
                    "headers": {"Content-Type": "application/json"},
                },
                "retry_config": {"retry_count": 2, "max_retry_duration": "180s"},
            },
            {
                "name": f"{parent}/jobs/morning-briefing",
                "description": "Morning briefing with today's schedule",
                "schedule": "0 8 * * 1-5",  # 8 AM, weekdays
                "time_zone": "America/New_York",
                "http_target": {
                    "uri": f"{service_url}/morning-briefing",
                    "http_method": "POST",
                    "headers": {"Content-Type": "application/json"},
                },
                "retry_config": {"retry_count": 2, "max_retry_duration": "120s"},
            },
        ]

        created_jobs = []
        for job_config in jobs:
            try:
                response = client.create_job(
                    request={"parent": parent, "job": job_config}
                )
                job_name = job_config["name"].split("/")[-1]
                print(f"✅ Created scheduler job: {job_name}")
                created_jobs.append(response.name)
            except Exception as e:
                job_name = job_config["name"].split("/")[-1]
                if "already exists" in str(e):
                    print(f"⚠️ Job already exists: {job_name}")
                else:
                    print(f"❌ Error creating job {job_name}: {e}")

        print("✅ Cloud Scheduler setup complete")
        return created_jobs

    except Exception as e:
        print(f"❌ Error setting up Cloud Scheduler: {e}")
        return []


def setup_email_templates():
    """Set up default email templates in Firestore"""
    try:
        db = firestore.Client()

        templates = [
            {
                "name": "meeting_confirmation",
                "subject": "Meeting Confirmed: {subject} - {date}",
                "body": """Hi {attendee_name},

Your meeting has been confirmed for {date_time}.

Meeting Details:
- Subject: {subject}
- Duration: {duration} minutes
- Location: {location}
- Meeting Link: {meeting_link}

If you need to reschedule, please let me know as soon as possible.

Best regards,
AI Executive Assistant""",
                "variables": [
                    "attendee_name",
                    "date_time",
                    "subject",
                    "duration",
                    "location",
                    "meeting_link",
                ],
            },
            {
                "name": "auto_response_general",
                "subject": "Re: {original_subject}",
                "body": """Thank you for your email.

I've received your message and will ensure it gets the appropriate attention. If this is urgent, please don't hesitate to call directly.

You can expect a response within 24 hours for non-urgent matters.

Best regards,
AI Executive Assistant""",
                "variables": ["original_subject"],
            },
            {
                "name": "meeting_reschedule",
                "subject": "Meeting Rescheduled: {subject}",
                "body": """Hi {attendee_name},

Your meeting "{subject}" originally scheduled for {original_time} has been rescheduled to {new_time}.

Updated Meeting Details:
- New Date/Time: {new_time}
- Duration: {duration} minutes
- Location: {location}

Please confirm if this new time works for you.

Best regards,
AI Executive Assistant""",
                "variables": [
                    "attendee_name",
                    "subject",
                    "original_time",
                    "new_time",
                    "duration",
                    "location",
                ],
            },
        ]

        for template in templates:
            template["created_at"] = firestore.SERVER_TIMESTAMP
            template["active"] = True
            db.collection("email_templates").add(template)
            print(f"✅ Created email template: {template['name']}")

        print("✅ Email templates setup complete")
        return True

    except Exception as e:
        print(f"❌ Error setting up email templates: {e}")
        return False


def setup_user_preferences():
    """Set up default user preferences"""
    try:
        db = firestore.Client()

        default_preferences = {
            "business_hours": {
                "start": "09:00",
                "end": "17:00",
                "timezone": "America/New_York",
                "working_days": [1, 2, 3, 4, 5],  # Monday-Friday
            },
            "meeting_defaults": {
                "default_duration": 30,
                "buffer_time": 15,
                "max_daily_meetings": 8,
                "preferred_meeting_times": ["10:00", "11:00", "14:00", "15:00"],
            },
            "email_settings": {
                "auto_respond": True,
                "response_delay_minutes": 5,
                "urgent_keywords": ["urgent", "asap", "emergency", "critical"],
                "max_auto_responses_per_day": 20,
            },
            "notification_settings": {
                "urgent_email_notification": True,
                "daily_summary": True,
                "meeting_reminders": True,
            },
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

        db.collection("user_preferences").document("default").set(default_preferences)
        print("✅ User preferences setup complete")
        return True

    except Exception as e:
        print(f"❌ Error setting up user preferences: {e}")
        return False


def main():
    """Run complete cloud setup"""
    print("🚀 Starting AI Executive Assistant cloud setup...")

    # Check environment variables
    required_env_vars = ["GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY", "SENDGRID_API_KEY"]

    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        print(f"❌ Missing environment variables: {missing_vars}")
        return False

    success_count = 0

    # Setup Firestore
    if setup_firestore():
        success_count += 1

    # Setup email templates
    if setup_email_templates():
        success_count += 1

    # Setup user preferences
    if setup_user_preferences():
        success_count += 1

    # Setup Cloud Scheduler
    jobs = setup_cloud_scheduler()
    if jobs:
        success_count += 1

    print(f"\n🎉 Setup complete! {success_count}/4 components successful")

    if success_count == 4:
        print("\n✅ Your AI Executive Assistant is ready to deploy!")
        print("\nNext steps:")
        print("1. Deploy to Cloud Run: gcloud run deploy --source .")
        print("2. Test endpoints: curl https://your-service-url.run.app/")
        print("3. Monitor logs: gcloud run logs tail")
        return True
    else:
        print("\n⚠️ Some components failed to setup. Check the logs above.")
        return False


if __name__ == "__main__":
    main()
