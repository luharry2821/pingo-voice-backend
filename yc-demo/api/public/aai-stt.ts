import { handleStt, preflight } from "../../server/handlers.js";
export const POST = (request: Request) => handleStt(request);
export const OPTIONS = () => preflight();
