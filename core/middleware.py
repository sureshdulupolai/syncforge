from django.utils.deprecation import MiddlewareMixin

class UILoaderMiddleware(MiddlewareMixin):
    """
    Injects a professional top-bar UI loader (NProgress) into all HTML responses.
    This provides a seamless loading experience without artificial sleeps.
    """
    def process_response(self, request, response):
        if 'text/html' in response.get('Content-Type', ''):
            loader_script = b"""
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/nprogress/0.2.0/nprogress.min.css" />
            <style>
                #nprogress .bar { background: #2563EB !important; height: 3px !important; }
                #nprogress .peg { box-shadow: 0 0 10px #2563EB, 0 0 5px #2563EB !important; }
                #nprogress .spinner-icon { display: none !important; }
            </style>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/nprogress/0.2.0/nprogress.min.js"></script>
            <script>
                NProgress.configure({ showSpinner: false, speed: 400, minimum: 0.1 });
                
                // Start immediately since this is injected on load (for subsequent page loads, the browser will show it if we hook links)
                // Actually, if we are here, the page has just loaded, so we should finish the progress bar.
                NProgress.done();

                document.addEventListener('DOMContentLoaded', () => {
                    // Intercept clicks on internal links to show loader
                    document.querySelectorAll('a[href]:not([target="_blank"]):not([href^="mailto:"]):not([href^="tel:"]):not([href^="#"])').forEach(a => {
                        a.addEventListener('click', (e) => {
                            if(!e.ctrlKey && !e.metaKey && !e.shiftKey) { NProgress.start(); }
                        });
                    });
                    // Intercept form submissions
                    document.querySelectorAll('form').forEach(f => {
                        f.addEventListener('submit', () => NProgress.start());
                    });
                });
                
                // Handle back/forward cache navigation
                window.addEventListener('pageshow', (e) => {
                    if (e.persisted) { NProgress.done(); }
                });
            </script>
            """
            
            content = response.content
            if b'</body>' in content:
                content = content.replace(b'</body>', loader_script + b'\n</body>')
                response.content = content
                if 'Content-Length' in response:
                    response['Content-Length'] = len(response.content)
        return response
