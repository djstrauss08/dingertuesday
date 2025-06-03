# Vercel Deployment Guide

This guide will help you deploy your MLB Stats Flask application to Vercel.

## Prerequisites

1. **Vercel Account**: Sign up at [vercel.com](https://vercel.com)
2. **Vercel CLI**: Already installed via `npm install -g vercel`
3. **Git Repository**: Your code should be in a Git repository

## Step 1: Set up Environment Variables on Vercel

Before deploying, you need to configure the environment variables on Vercel:

1. Go to your Vercel dashboard
2. Create a new project or select your existing project
3. Go to Settings > Environment Variables
4. Add the following environment variables:

### Required Environment Variables:

```
SECRET_KEY=your-super-secret-key-here
FLASK_ENV=production
DATABASE_PATH=/tmp/daily_mlb_data.sqlite
FIREBASE_PROJECT_ID=dingertuesday-18a26
```

### Firebase Configuration (Optional - for authentication):
If you want to use Firebase authentication in production:

```
FIREBASE_PRIVATE_KEY_ID=your-private-key-id
FIREBASE_PRIVATE_KEY=your-private-key-with-newlines
FIREBASE_CLIENT_EMAIL=your-client-email
FIREBASE_CLIENT_ID=your-client-id
```

## Step 2: Deploy to Vercel

### Option A: Deploy via CLI

```bash
# Login to Vercel
vercel login

# Deploy to production
vercel --prod
```

### Option B: Deploy via Git Integration

1. Connect your GitHub/GitLab repository to Vercel
2. Push your code to the main branch
3. Vercel will automatically deploy

## Step 3: Configure Domain (Optional)

1. In your Vercel dashboard, go to Settings > Domains
2. Add your custom domain or use the provided vercel.app URL

## Important Notes

### Database Considerations
- **SQLite Limitations**: Vercel's serverless functions are stateless, so SQLite data won't persist between function calls
- **Recommended**: For production, consider using:
  - PostgreSQL (Vercel Postgres)
  - MongoDB Atlas
  - Firebase Firestore
  - Supabase

### Scheduler Limitations
- Background schedulers don't work in serverless environments
- Consider using:
  - Vercel Cron Jobs
  - GitHub Actions with scheduled workflows
  - External cron services (cron-job.org)

### File Uploads
- Static files should be handled by cloud storage (AWS S3, Cloudinary, etc.)
- `/tmp` directory in serverless functions is limited and temporary

## Troubleshooting

### Common Issues:

1. **Module Import Errors**
   - Ensure all dependencies are in `requirements.txt`
   - Check Python version compatibility

2. **Database Errors**
   - SQLite files won't persist in serverless
   - Use `/tmp/` for temporary storage

3. **Timeout Errors**
   - MLB API calls can be slow
   - Consider increasing function timeout in `vercel.json`

4. **Firebase Authentication**
   - Ensure all environment variables are properly set
   - Check Firebase project configuration

### Logs
Check deployment logs in your Vercel dashboard under:
Functions > [your-function] > View Function Logs

## Production Optimization

### For better performance:

1. **Use a Persistent Database**
   ```bash
   # Add Vercel Postgres
   vercel postgres create
   ```

2. **Enable Caching**
   - Use Redis for caching MLB API responses
   - Implement proper cache headers

3. **Optimize Bundle Size**
   - Remove unused dependencies
   - Use `.vercelignore` to exclude unnecessary files

## Monitoring

- Monitor function execution time
- Set up alerts for errors
- Monitor database performance

## Next Steps

1. Test all functionality after deployment
2. Set up monitoring and alerts
3. Configure custom domain
4. Set up CI/CD pipeline
5. Consider migrating to a persistent database solution

For more help, check the [Vercel documentation](https://vercel.com/docs) or create an issue in your repository. 