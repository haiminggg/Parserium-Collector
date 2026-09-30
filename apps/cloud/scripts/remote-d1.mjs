export function remoteDatabase(env) {
  const account=env.CLOUDFLARE_ACCOUNT_ID,database=env.CLOUDFLARE_D1_DATABASE_ID,token=env.CLOUDFLARE_API_TOKEN;
  if(!/^[a-f0-9]{32}$/.test(account || '') || !/^[a-f0-9-]{36}$/.test(database || '') || !token?.trim()) throw Error('Invalid operator configuration');
  return {prepare(sql) {return {bind(...params) {return {async run() {
    const response=await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/d1/database/${database}/query`,{
      method:'POST',redirect:'error',signal:AbortSignal.timeout(15000),
      headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({sql,params})});
    if(!response.ok) throw Error('D1 operation failed');
    const body=await response.json();
    if(body.success!==true || body.result?.length!==1 || body.result[0].success!==true) throw Error('D1 operation failed');
    return body.result[0];
  }}}}}};
}
