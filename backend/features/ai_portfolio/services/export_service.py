
from core.config import settings
from features.ai_portfolio.repositories import portfolio_repo
from features.ai_portfolio.services.render_service import RenderService
from git import Repo
from github import Github

class ExportService:
    def __init__(self):
        self.GITHUB_TOKEN=settings.GITHUB_TOKEN
        self.GITHUB_USERNAME=settings.GITHUB_USERNAME
        self.REPO_NAME=settings.REPO_NAME
        self.g = Github(self.GITHUB_TOKEN)


    async def export_to_cloudflare(self, html_content: str, slug: str):
        # Create a new repository
        user = self.g.get_user()
        repo = user.get_repo(self.REPO_NAME)

        # Add the rendered HTML as an index.html file in the repository
        repo.create_file(f"{slug}.html", "Add portfolio HTML file", html_content)

        return f"https://growza-portfolios.pages.dev/{slug}"
    
    async def delete_from_cloudflare(self, slug: str):
        user = self.g.get_user()
        repo = user.get_repo(self.REPO_NAME)

        # Find the file in the repository
        contents = repo.get_contents(f"{slug}.html")
        repo.delete_file(contents.path, "Delete portfolio HTML file", contents.sha)
        

    async def export_portfolio(portfolio_id: str, template_id: int):
        portfolio = await portfolio_repo.get_portfolio(portfolio_id)
        if not portfolio:
            return None

        rendered_html = RenderService.render_portfolio(
            portfolio=portfolio["data"],
            template_id=template_id,
        )

        return rendered_html
    
export_service = ExportService()