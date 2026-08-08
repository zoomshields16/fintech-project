// Where the browser sends API calls.
//
// Served from a real domain, the pages talk to the deployed backend; opened locally
// (Live Server, or the file directly, which reports an empty hostname) they talk to a
// backend on this machine. That keeps local development working with no build step and
// no edit-before-you-commit ritual.
//
// This lives in ONE file because the address changes again when the custom domain lands.
// It was previously copy-pasted into four pages, and index.html was already missed once.
const LOCAL_HOSTNAMES = ['localhost', '127.0.0.1', ''];

const API = LOCAL_HOSTNAMES.includes(location.hostname)
    ? 'http://127.0.0.1:8000'
    : 'https://api.theretailanalyst.com';
