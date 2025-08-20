#!/bin/bash

# deploy.sh - Complete deployment script for AI Executive Assistant

set -e  # Exit on any error

echo "🚀 AI Executive Assistant Deployment Script"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ID=${GOOGLE_CLOUD_PROJECT:-"your-project-id"}
SERVICE_NAME="ai-executive-assistant"
REGION="us-central1"
SERVICE_ACCOUNT_NAME="ai-assistant-sa"

# Function to print colored output
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check if required commands exist
check_dependencies() {
    echo "🔍 Checking dependencies..."
    
    commands=("gcloud" "python3" "pip")
    for cmd in "${commands[@]}"; do
        if ! command -v $cmd &> /dev/null; then
            print_error "$cmd is required but not installed"
            exit 1
        fi
    done
    print_status "All dependencies found"
}

# Check environment variables
check_environment() {
    echo "🔍 Checking environment variables..."
    
    required_vars=("GEMINI_API_KEY" "SENDGRID_API_KEY")
    missing_vars=()
    
    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            missing_vars+=($var)
        fi
    done
    
    if [ ${#missing_vars[@]} -ne 0 ]; then
        print_error "Missing required environment variables:"
        for var in "${missing_vars[@]}"; do
            echo "  - $var"
        done
        echo ""
        echo "Please set these environment variables and try again:"
        echo "export GEMINI_API_KEY='your_key_here'"
        echo "export SENDGRID_API_KEY='your_key_here'"
        exit 1
    fi
    
    print_status "Environment variables configured"
}

# Setup Google Cloud project
setup_gcp() {
    echo "☁️ Setting up Google Cloud..."
    
    # Set project
    gcloud config set project $PROJECT_ID
    print_status "Project set to $PROJECT_ID"
    
    # Enable required APIs
    echo "Enabling required APIs..."
    apis=(
        "run.googleapis.com"
        "cloudbuild.googleapis.com"
        "cloudscheduler.googleapis.com"
        "firestore.googleapis.com"
        "gmail.googleapis.com"
        "calendar-json.googleapis.com"
    )
    
    for api in "${apis[@]}"; do
        if gcloud services list --enabled --filter="name:$api" --format="value(name)" | grep -q "$api"; then
            print_status "$api already enabled"
        else
            gcloud services enable $api
            print_status "Enabled $api"
        fi
    done
}

# Create service account
create_service_account() {
    echo "👤 Setting up service account..."
    
    # Check if service account exists
    if gcloud iam service-accounts describe "${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" &>/dev/null; then
        print_warning "Service account already exists"
    else
        gcloud iam service-accounts create $SERVICE_ACCOUNT_NAME \
            --display-name="AI Executive Assistant" \
            --description="Service account for AI Executive Assistant"
        print_status "Service account created"
    fi
    
    # Grant necessary roles
    roles=(
        "roles/firestore.user"
        "roles/cloudscheduler.admin"
        "roles/gmail.readonly"
        "roles/calendar.events.owner"
    )
    
    for role in "${roles[@]}"; do
        gcloud projects add-iam-policy-binding $PROJECT_ID \
            --member="serviceAccount:${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
            --role="$role" --quiet
        print_status "Granted $role"
    done
}

# Setup Firestore
setup_firestore() {
    echo "🗄️ Setting up Firestore..."
    
    # Check if Firestore is already setup
    if gcloud firestore databases list --format="value(name)" 2>/dev/null | grep -q "projects/$PROJECT_ID"; then
        print_warning "Firestore database already exists"
    else
        gcloud firestore databases create --region=$REGION --type=firestore-native
        print_status "Firestore database created"
    fi
    
    # Run cloud setup script
    echo "Initializing database collections..."
    python3 setup_cloud.py
}

# Build and deploy to Cloud Run
deploy_service() {
    echo "🏗️ Building and deploying service..."
    
    # Create .gcloudignore if it doesn't exist
    if [ ! -f .gcloudignore ]; then
        cat > .gcloudignore << EOF
.git
.gitignore
README.md
__pycache__/
*.pyc
*.pyo
*.pyd
.pytest_cache/
.coverage
.env
.venv/
venv/
.DS_Store
*.log
EOF
        print_status "Created .gcloudignore"
    fi
    
    # Deploy to Cloud Run
    gcloud run deploy $SERVICE_NAME \
        --source . \
        --platform managed \
        --region $REGION \
        --allow-unauthenticated \
        --memory 1Gi \
        --cpu 1 \
        --max-instances 10 \
        --set-env-vars GOOGLE_CLOUD_PROJECT=$PROJECT_ID \
        --set-env-vars GEMINI_API_KEY=$GEMINI_API_KEY \
        --set-env-vars SENDGRID_API_KEY=$SENDGRID_API_KEY \
        --service-account="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
    
    print_status "Service deployed successfully"
}

# Get service URL
get_service_url() {
    SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')
    echo "SERVICE_URL=$SERVICE_URL"
}

# Test deployment
test_deployment() {
    echo "🧪 Testing deployment..."
    
    get_service_url
    
    if [ -z "$SERVICE_URL" ]; then
        print_error "Could not get service URL"
        exit 1
    fi
    
    # Test health endpoint
    if curl -s -f "$SERVICE_URL/" > /dev/null; then
        print_status "Health check passed"
    else
        print_error "Health check failed"
        exit 1
    fi
    
    # Test API endpoints
    echo "Testing API endpoints..."
    
    endpoints=("/activities" "/daily-summary" "/calendar/availability")
    for endpoint in "${endpoints[@]}"; do
        if curl -s -f "$SERVICE_URL$endpoint" > /dev/null; then
            print_status "Endpoint $endpoint working"
        else
            print_warning "Endpoint $endpoint may need authentication"
        fi
    done
}

# Setup OAuth (for Gmail/Calendar access)
setup_oauth() {
    echo "🔐 OAuth Setup Instructions"
    echo "=========================="
    echo ""
    echo "To enable Gmail and Calendar access, you need to:"
    echo ""
    echo "1. Go to Google Cloud Console:"
    echo "   https://console.cloud.google.com/apis/credentials?project=$PROJECT_ID"
    echo ""
    echo "2. Click 'Create Credentials' > 'OAuth 2.0 Client IDs'"
    echo ""
    echo "3. Configure OAuth consent screen (if not done already)"
    echo ""
    echo "4. Create OAuth client with these settings:"
    echo "   - Application type: Web application"
    echo "   - Authorized redirect URIs: $SERVICE_URL/auth/callback"
    echo ""
    echo "5. Download the credentials JSON and set as GOOGLE_CREDENTIALS_JSON env var"
    echo ""
    print_warning "OAuth setup is required for full functionality"
}

# Display final information
show_completion() {
    echo ""
    echo "🎉 Deployment Complete!"
    echo "======================"
    echo ""
    echo "Service URL: $SERVICE_URL"
    echo ""
    echo "Available endpoints:"
    echo "  GET  $SERVICE_URL/                    - Health check"
    echo "  POST $SERVICE_URL/process-emails      - Process emails"
    echo "  POST $SERVICE_URL/schedule-meeting    - Schedule meeting"
    echo "  GET  $SERVICE_URL/daily-summary       - Get daily summary"
    echo "  GET  $SERVICE_URL/activities          - Get activities"
    echo "  GET  $SERVICE_URL/calendar/availability - Check availability"
    echo ""
    echo "Monitoring:"
    echo "  Logs:    gcloud run logs tail --service=$SERVICE_NAME --region=$REGION"
    echo "  Metrics: https://console.cloud.google.com/run/detail/$REGION/$SERVICE_NAME"
    echo ""
    echo "Scheduled jobs will run automatically:"
    echo "  - Email processing: Every 30 minutes (business hours)"
    echo "  - Daily summary: 6 PM weekdays"
    echo "  - Morning briefing: 8 AM weekdays"
    echo ""
    print_status "Your AI Executive Assistant is now live!"
}

# Main execution
main() {
    echo "Starting deployment process..."
    echo ""
    
    check_dependencies
    check_environment
    setup_gcp
    create_service_account
    setup_firestore
    deploy_service
    test_deployment
    
    get_service_url
    setup_oauth
    show_completion
    
    echo ""
    print_status "Deployment completed successfully! 🎉"
}

# Handle script interruption
trap 'echo ""; print_error "Deployment interrupted"; exit 1' INT

# Run main function
main "$@"