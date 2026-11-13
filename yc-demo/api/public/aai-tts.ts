import { handleTts, preflight } from "../../server/handlers.js";
export const POST = (request: Request) => handleTts(request);
export const OPTIONS = () => preflight();
