import re
import timeit

def extract_urls_current(text):
    """Extracts URLs from a string using regex."""
    url_pattern = re.compile(r'https?://[^\s]+')
    return url_pattern.findall(text)

URL_PATTERN = re.compile(r'https?://[^\s]+')
def extract_urls_optimized(text):
    """Extracts URLs from a string using regex."""
    return URL_PATTERN.findall(text)

test_text = "Check out https://google.com and http://example.com/path?query=1 for more info. Also https://github.com/google-gemini."

def benchmark():
    iterations = 100000

    current_time = timeit.timeit(lambda: extract_urls_current(test_text), number=iterations)
    optimized_time = timeit.timeit(lambda: extract_urls_optimized(test_text), number=iterations)

    print(f"Current implementation: {current_time:.4f} seconds for {iterations} iterations")
    print(f"Optimized implementation: {optimized_time:.4f} seconds for {iterations} iterations")
    print(f"Improvement: {(current_time - optimized_time) / current_time * 100:.2f}%")

if __name__ == "__main__":
    benchmark()
