# 🚀 Growza 

Flutter-based mobile application for career development and job matching.

## 📱 Features

- **Resume Optimization**: AI-powered resume enhancement and suggestions
- **Job Matching**: Smart job recommendations based on skills and preferences
- **Career Build**: Personalized career path planning and guidance
- **Mock Interview**: Practice interviews with AI feedback
- **Market Insight**: Industry trends and salary insights
- **AI Portfolio**: Automated portfolio generation and optimization

## 🛠️ Tech Stack

- **Framework**: Flutter 3.x
- **State Management**: Riverpod
- **Authentication**: Supabase Auth
- **Backend Communication**: FastAPI (Python)
- **HTTP Client**: Dio
- **Navigation**: GoRouter

## 📁 Project Structure

```
lib/
├── core/                    # Core utilities and configurations
│   ├── constants/          # App constants (colors, strings, API endpoints)
│   ├── theme/              # App theme configuration
│   ├── utils/              # Helper functions and validators
│   ├── errors/             # Error handling
│   └── network/            # Network configurations
├── shared/                  # Shared resources
│   ├── widgets/            # Reusable widgets
│   └── models/             # Shared data models
├── services/                # Services layer
│   ├── supabase_service.dart
│   ├── auth_service.dart
│   └── api_service.dart
└── features/                # Feature modules
    ├── auth/
    ├── resume_optimization/
    ├── job_matching/
    ├── career_build/
    ├── mock_interview/
    ├── market_insight/
    └── ai_portfolio/
```

## 🚀 Getting Started

### Prerequisites

- Flutter SDK (>=3.0.0)
- Dart SDK
- Android Studio / VS Code
- Git

### Installation

1. Clone the repository:
```bash
git clone https://github.com/Shorouqtareq982/Advisor_Career_App.git
cd Advisor_Career_App/frontend
```

2. Install dependencies:
```bash
flutter pub get
```

3. Create `.env` file in the root directory:
```env
SUPABASE_URL=your_supabase_url
SUPABASE_ANON_KEY=your_supabase_anon_key
FASTAPI_BASE_URL=your_fastapi_backend_url
```

4. Run the app:
```bash
flutter run
```

## 🔧 Development

### Running Tests
```bash
flutter test
```

### Building for Production
```bash
# Android
flutter build apk --release

# iOS
flutter build ios --release
```

## 👥 Team

- **Frontend Team**: Flutter + Supabase (2 developers)
- **Backend Team**: Python + FastAPI + Supabase (7 developers)

## 📝 Git Workflow

1. Create a new branch for each feature:
```bash
git checkout -b feature/feature-name
```

2. Make your changes and commit:
```bash
git add .
git commit -m "Description of changes"
```

3. Push to GitHub:
```bash
git push origin feature/feature-name
```

4. Create a Pull Request for review

## 🤝 Contributing

Please read our contributing guidelines before submitting pull requests.

## 📄 License

This project is part of the Career Advisory App initiative.

---

**Made with ❤️ by Growza Team**