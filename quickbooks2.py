from rauth import OAuth1Session, OAuth1Service
import xml.etree.ElementTree as ET
import xmltodict
from xml.dom import minidom
import requests, urllib
import collections, datetime, json, pandas, os, time
import textwrap # for uploading files

class QuickBooks():
    """A wrapper class around Python's Rauth module for Quickbooks the API"""

    session = None

    base_url_v3 =  "https://quickbooks.api.intuit.com/v3"

    request_token_url = "https://oauth.intuit.com/oauth/v1/get_request_token"
    access_token_url = "https://oauth.intuit.com/oauth/v1/get_access_token"
    authorize_url = "https://appcenter.intuit.com/Connect/Begin"

    # Added for token refreshing (within 30 days of expiry)
    # This is known in Intuit's parlance as a "Reconnect"
    _attemps_count = 5
    _namespace = "http://platform.intuit.com/api/v1"
    # See here for more:
    # https://developer.intuit.com/v2/docs/0100_accounting/
    # 0060_authentication_and_authorization/oauth_management_api

    # Things needed for authentication
    qbService = None

    def __init__(self, **args):
        if 'cred_path' in args:
            self.read_creds_from_file(args['cred_path'])

        self.consumer_key = args.get('consumer_key', '')
        self.consumer_secret = args.get('consumer_secret', '')
        self.callback_url = args.get('callback_url', '')

        self.access_token = args.get('access_token', '')
        self.access_token_secret = args.get('access_token_secret', '')

        self.request_token = args.get('request_token', '')
        self.request_token_secret = args.get('request_token_secret', '')

        self.expires_on = args.get("expire_date", args.get("expires_on"))

        if not self.expires_on:
            if self.verbosity > 0:
                print "No expiration date for this token!?"
                import ipdb;ipdb.set_trace()
        if isinstance(self.expires_on, (str, unicode)):
            self.expires_on = datetime.datetime.strptime(
                self.expires_on.replace("-","").replace("/",""),
                "%Y%m%d").date()

        self.reconnect_window_days_count = int(args.get(
            "reconnect_window_days_count", 30))
        self.acc_token_changed_callback = args.get(
            "acc_token_changed_callback", self.default_call_back)

        self.company_id = args.get('company_id', 0)

        self.verbosity = self.vb = args.get('verbosity', 0)

        self._BUSINESS_OBJECTS = [
            "Account", "Attachable", "Bill", "BillPayment",
            "Class", "CompanyInfo", "CreditMemo", "Customer",
            "Department", "Deposit", "Employee", "Estimate", "Invoice",
            "Item", "JournalEntry", "Payment", "PaymentMethod",
            "Preferences", "Purchase", "PurchaseOrder", "RefundReceipt",
            #"ReimburseCharge",
            "SalesReceipt", "TaxAgency", "TaxCode", "TaxRate",
            #"TaxService",
            "Term", "TimeActivity", "Transfer", "Vendor", "VendorCredit",]

        self._NAME_LIST_OBJECTS = [
            "Account", "Class", "Customer", "Department", "Employee", "Item",
            "PaymentMethod", "TaxCode", "TaxRate", "Term", "Vendor",]

        self._TRANSACTION_OBJECTS = [
            "Bill", "BillPayment", "CreditMemo", "Deposit",
            "Estimate", "Invoice", "JournalEntry", "Payment", "Purchase",
            #"PurchaseOrder",
            #"ReimburseCharge",
            "RefundReceipt", "SalesReceipt",
            #"TimeActivity",
            "Transfer", "VendorCredit",]

        # Sometimes in linked transactions, the API calls txn objects by
        #  another name
        self._biz_object_correctors = {
            "Bill"               : "Bill",
            "Check"              : "Purchase",
            "CreditCardCredit"   : "Purchase",
            "Credit Card Credit" : "Purchase",
            "Deposit"            : "Deposit",
            "Expense"            : "Purchase",
            "Invoice"            : "Invoice",
            "Journal Entry"      : "JournalEntry",
            "JournalEntry"       : "JournalEntry",
            "Payment"            : "Payment",
            "Vendor Credit"      : "VendorCredit",
            "VendorCredit"       : "VendorCredit",
            "CreditMemo"         : "CreditMemo",}

        # Before they even had QBO...
        self.latest = datetime.datetime(
            1980, 1, 1, 0, 0, 0).isoformat()+"-08:00"

    def _reconnect_if_time(self):
        current_date = datetime.date.today()
        days_diff = (self.expires_on - current_date).days
        if days_diff > 0:
            if days_diff <= self.reconnect_window_days_count:
                print "Going to reconnect %s..." % self.company_id
                if self._reconnect():
                    print "Reconnected %s successfully!" % self.company_id
                else:
                    print "For %s:" % self.company_id
                    print "Unable to reconnect, try again later, you have " \
                        "{} days left to do that".format(days_diff)
            elif self.verbosity > 1:
                #import ipdb;ipdb.set_trace()
                print "Days remaining on %s QBO Access Token: %s" % (
                    self.company_id, days_diff)
        else:
            raise Exception("The token is expired, unable to reconnect. " \
                "Please get a new one.")

    def default_call_back(self, access_token, access_token_secret, 
                       company_id, expires_on):
        """
        In case the caller of the QuickBooks session doesn't provide a callback
         function, new creds (after a reconnect) won't be ENTIRELY lost...
        """
        print "NEW CREDENTIALS (POST RECONNECT):"
        print "access_token:         {}".format(access_token)
        print "access_token_secreat: {}".format(access_token_secret)
        print "company_id:           {}".format(company_id)
        print "expires_on:           {}".format(expires_on)
        raw_input("Press <enter> to acknowledge and continue.")

    def _reconnect(self, i=1):
        if i > self._attemps_count:
            print "Unable to reconnect, there're no attempts left " \
                "({} attempts sent).".format(i)
            return False
        else:
            self._get_session()
            resp = self.session.request(
                "GET",
                "https://appcenter.intuit.com/api/v1/connection/reconnect",
                True, 
                self.company_id, 
                verify=True
            )
            dom = minidom.parseString(ET.tostring(ET.fromstring(resp.content),
                                                  "utf-8"))
            if resp.status_code == 200:
                error_code = int(dom.getElementsByTagNameNS(
                    self._namespace, "ErrorCode")[0].firstChild.nodeValue)
                if error_code == 0:
                    print "Reconnected successfully"

                    date_raw  = dom.getElementsByTagNameNS(
                        self._namespace, "ServerTime")[0].firstChild.nodeValue
                    from dateutil import parser
                    added_date = parser.parse(date_raw).date()
                    self.expires_on = added_date + datetime.timedelta(days=180)
                    
                    self.access_token = str(dom.getElementsByTagNameNS(
                        self._namespace, "OAuthToken")[0].firstChild.nodeValue)
                    self.access_token_secret = str(dom.getElementsByTagNameNS(
                        self._namespace,
                        "OAuthTokenSecret")[0].firstChild.nodeValue)
                    
                    if self.verbosity > 9 or \
                       not self.acc_token_changed_callback:
                        print "at, ats, cid, expires_on:"
                        print self.access_token
                        print self.access_token_secret
                        print self.company_id
                        print self.expires_on
                        raw_input("Press <enter> to continue")

                    self.acc_token_changed_callback(
                        self.access_token, 
                        self.access_token_secret,
                        self.company_id,
                        self.expires_on
                    )

                    return True

                else:
                    msg = str(dom.getElementsByTagNameNS(
                        self._namespace, 
                        "ErrorMessage")[0].firstChild.nodeValue)
                    
                    print "An error occurred while trying to reconnect, code:" \
                        "{}, message: \"{}\"".format(error_code, msg)

                    i += 1

                    print "Trying to reconnect again... attempt #{}".format(i)

                    self._reconnect(i)
            else:
                print "An HTTP error {} occurred,".format(resp.status_code) \
                    + "trying again, attempt #{}".format(i)

                i += 1
                self._reconnect(i)

    def _get_session(self):
        if not self.session:
            self.create_session()    # sets self.session...
            self._reconnect_if_time()
            
        return self.session

    def get_authorize_url(self):
        """Returns the Authorize URL as returned by QB,
        and specified by OAuth 1.0a.
        :return URI:
        """
        self.qbService = OAuth1Service(
                name = None,
                consumer_key = self.consumer_key,
                consumer_secret = self.consumer_secret,
                request_token_url = self.request_token_url,
                access_token_url = self.access_token_url,
                authorize_url = self.authorize_url,
                base_url = None
            )

        rt, rts = self.qbService.get_request_token(
            params={'oauth_callback':self.callback_url}
        )

        self.request_token, self.request_token_secret = [rt, rts]

        return self.qbService.get_authorize_url(self.request_token)

    def get_access_tokens(self, oauth_verifier):
        """Wrapper around get_auth_session, returns session, and sets
        access_token and access_token_secret on the QB Object.
        :param oauth_verifier: the oauth_verifier as specified by OAuth 1.0a
        """
        session = self.qbService.get_auth_session(
                self.request_token,
                self.request_token_secret,
                data={'oauth_verifier': oauth_verifier})

        self.access_token = session.access_token
        self.access_token_secret = session.access_token_secret

        return session

    def create_session(self):
        if self.consumer_secret and self.consumer_key and \
           self.access_token_secret and self.access_token:
            self.session = OAuth1Session(self.consumer_key,
                                         self.consumer_secret,
                                         self.access_token,
                                         self.access_token_secret)

        else:
            # shouldn't there be a workflow somewhere to GET the auth tokens?
            # add that or ask someone on oDesk to build it...
            raise Exception("Need four creds for Quickbooks.create_session.")

        return self.session

    def query_fetch_more(self, r_type, header_auth, realm,
                         qb_object, original_payload =''):
        """
        Wrapper script around hammer_it (previously keep_trying) to fetch more
         results if there are more.
        """
        # 500 is the maximum number of results returned by QB
        # Or is it 1,000? Hmmm...
        max_results = 1000
        start_position = 1
        more = True
        data_set = []
        url = self.base_url_v3 + "/company/%s/query" % self.company_id

        # Edit the payload to return more results.

        payload = original_payload + " MAXRESULTS " + str(max_results)

        while more:
            if self.verbosity > 4:
                print payload

            # Don't keep an extra method around and have to maintain both
            """
            r_dict = self.keep_trying(r_type, url, True, 
                                      self.company_id, payload)
            """
            r_dict = self.hammer_it(r_type, url, payload, "text")
            
            try:
                if "count(*)" in payload.lower():
                    return r_dict['QueryResponse']["totalCount"]
                else:
                    access = r_dict['QueryResponse'][qb_object]
            except:
                if 'QueryResponse' in r_dict and r_dict['QueryResponse'] == {}:
                    #print "Query OK, no results: %s" % r_dict['QueryResponse']
                    return data_set
                else:
                    print "FAILED",
                    #import ipdb;ipdb.set_trace()
                    print json.dumps(r_dict, indent=4)
                    """
                    r_dict = self.keep_trying(r_type,
                                              url,
                                              True,
                                              self.company_id,
                                              payload)
                    """
                    r_dict = self.hammer_it(r_type, url, payload, "json")
                    
            # For some reason the totalCount isn't returned for some queries,
            # in that case, check the length, even though that actually requires
            # measuring
            try:
                result_count = int(r_dict['QueryResponse']['totalCount'])
                if result_count < max_results:
                    more = False
            except KeyError:
                try:
                    result_count = len(r_dict['QueryResponse'][qb_object])
                    if result_count < max_results:
                        more = False
                except KeyError:
                    print "\n\n ERROR", r_dict
                    pass


            if self.verbosity > 3:
                print "({} batch begins with record {:7} and contains ".format(
                    qb_object, start_position) + "{:4} records)".format(
                        result_count)

            start_position = start_position + max_results
            payload = "{} STARTPOSITION {} MAXRESULTS {}".format(
                original_payload, start_position, max_results)

            try:
                data_set += r_dict['QueryResponse'][qb_object]
            except KeyError:
                if self.verbosity > 0:
                    import traceback;traceback.print_exc()
                    
                raise Exception("QBO Query Failed")

        return data_set
        
    def create_object(self, qbbo, create_dict, content_type = "json"):
        """
        One of the four glorious CRUD functions.
        Getting this right means using the correct object template and
        and formulating a valid request_body. This doesn't help with that.
        It just submits the request and adds the newly-created object to the
        session's brain.
        """

        if qbbo not in self._BUSINESS_OBJECTS:
            raise Exception("%s is not a valid QBO Business Object." % qbbo,
                            " (Note that this validation is case sensitive.)")

        url = "https://qb.sbfinance.intuit.com/v3/company/%s/%s" % \
              (self.company_id, qbbo.lower())

        request_body = json.dumps(create_dict, indent=4)

        if self.verbosity > 0:
            if qbbo in ["Employee", "Vendor"]:
                reffer = "called %s" % create_dict.get("DisplayName")
            elif qbbo in ["Account", "Customer", "Item"]:
                reffer = "called %s" % create_dict.get("FullyQualifiedName")
            else:
                reffer = "labeled %s" % create_dict.get(
                    "DocNumber", "<no DocNumber>")
                
            print "About to create a(n) %s object (%s)." % (qbbo, reffer)
                                                                    
            if self.verbosity > 5:
                print "Here's the request_body:"
                print request_body

        response = self.hammer_it("POST", url, request_body, content_type)

        if qbbo in response:
            new_object = response[qbbo]

        else:
            if self.verbosity > 0:
                print "It looks like the create failed for this {}.".format(
                    qbbo)

            return None

        new_Id     = new_object["Id"]

        attr_name = qbbo+"s"

        if not hasattr(self, attr_name):
            if self.verbosity > 3:
                print "Creating a %ss attribute for this session." % qbbo

            self.get_objects(qbbo).update({new_Id:new_object})

        else:
            if self.verbosity > 3:
                print "Adding this new %s to the existing set of them." \
                    % qbbo
                print json.dumps(new_object, indent=4)
                
            getattr(self, attr_name)[new_Id] = new_object

        self.latest = max(
            self.latest, new_object["MetaData"]["LastUpdatedTime"])
            
        return new_object

    def read_object(self, qbbo, object_id, content_type = "json"):
        """Makes things easier for an update because you just do a read,
        tweak the things you want to change, and send that as the update
        request body (instead of having to create one from scratch)."""

        if qbbo not in self._BUSINESS_OBJECTS:
            if qbbo in self._biz_object_correctors:
                qbbo = self._biz_object_correctors[qbbo]
            
            else:
                raise Exception("No business object called %s" \
                                % qbbo)

        Id = str(object_id).replace(".0","")

        url = "https://quickbooks.api.intuit.com/v3/company/%s/%s/%s" % \
              (self.company_id, qbbo.lower(), Id)

        if self.verbosity > 0:
            print "Reading %s %s." % (qbbo, Id)

        response = self.hammer_it("GET", url, None, content_type)

        if not qbbo in response:
            if self.verbosity > 0:
                print "It looks like the read failed for {} {}.".format(
                    qbbo, object_id)

            return None

        return response[qbbo]

    def update_object(self, qbbo, Id, update_dict, content_type = "json"):
        """
        Generally before calling this, you want to call the read_object
        command on what you want to update. The alternative is forming a valid
        update request_body from scratch, which doesn't look like fun to me.
        """
        
        Id = str(Id).replace(".0","")

        if qbbo not in self._BUSINESS_OBJECTS:
            raise Exception("%s is not a valid QBO Business Object." % qbbo,
                            " (Note that this validation is case sensitive.)")

        """
        url = "https://qb.sbfinance.intuit.com/v3/company/%s/%s" % \
              (self.company_id, qbbo.lower()) + "?operation=update"

        url = "https://quickbooks.api.intuit.com/v3/company/%s/%s" % \
              (self.company_id, qbbo.lower()) + "?requestid=%s" % Id
        """

        #see this link for url troubleshooting info:
        #http://stackoverflow.com/questions/23333300/whats-the-correct-uri-
        # for-qbo-v3-api-update-operation/23340464#23340464

        url = "https://quickbooks.api.intuit.com/v3/company/%s/%s" % \
              (self.company_id, qbbo.lower())

        '''
        #work from the existing account json dictionary
        e_dict = self.get_objects(qbbo)[str(Id)]
        e_dict.update(update_dict)
        '''
        # NO! DON'T DO THAT, THEN YOU CAN'T DELETE STUFF YOU WANT TO DELETE!

        e_dict = update_dict
        request_body = json.dumps(e_dict, indent=4)

        if self.verbosity > 0:
            if qbbo in ["Employee", "Vendor"]:
                reffer = "called %s" % e_dict.get("DisplayName")
            elif qbbo in ["Account", "Class", "Customer", "Item"]:
                reffer = "called %s" % e_dict.get("Name")
            elif qbbo in ["Attachable"]:
                reffer = "called %s" % e_dict.get("FileName", "<no FileName>")
            else:
                reffer = "labeled %s" % e_dict.get(
                    "DocNumber", "<no DocNumber>")

            print "About to update %s Id %s (%s)." % (qbbo, Id, reffer)
                
            if self.verbosity > 5:
                print "Here's the request body:"
                print request_body

            if self.verbosity > 9:
                raw_input("Waiting...")

        response = self.hammer_it("POST", url, request_body, content_type)

        if qbbo in response:
            new_object = response[qbbo]

        else:
            if self.verbosity > 0:
                print "It looks like the update failed for {} {}.".format(
                    qbbo, Id)

            return None

        attr_name = qbbo+"s"

        if not hasattr(self,attr_name):
            if self.verbosity > 3:
                print "Creating a %ss attribute for this session." % qbbo

            self.get_objects(qbbo)

        else:
            if self.verbosity > 3:
                print "Adding this new %s to the existing set of them." \
                    % qbbo
                print json.dumps(new_object, indent=4)

            getattr(self, attr_name)[Id] = new_object

        self.latest = max(
            self.latest, new_object["MetaData"]["LastUpdatedTime"])
            
        return new_object

    def delete_object(self, qbbo, object_id = None, content_type = "json",
                      json_dict = None):
        """
        Don't need to give it an Id, just the whole object as returned by
        a read operation.
        """
        attr_name = qbbo+"s"
        if not hasattr(self, attr_name):
            setattr(self, attr_name, collections.OrderedDict())
        
        if object_id:
            Id = str(object_id).replace(".0","")
            json_dict = self.read_object(qbbo, Id)
            if not json_dict:
                # There was a read problem...assume the object doesn't even
                #  exist anymore (if it ever did)
                if object_id in getattr(self, attr_name):
                    del(getattr(self, attr_name)[object_id])
                return {"Synthetic Response" :
                        "qbo.py failed to read object, which may not exist."}
        elif json_dict:
            Id = json_dict["Id"]
        else:
            raise Exception("Need either an Id or an existing object dict!")
                        
        if hasattr(self, attr_name) and not Id in getattr(self, attr_name):
            # It's already been deleted (or was never there)
            return {"Synthetic Response" :
                    "Object Was Already Gone / Never There"}
        
        if not 'Id' in json_dict:
            print json.dumps(json_dict, indent=4)

            raise Exception("No Id attribute found in the above dict!")

        request_body = json.dumps(json_dict, indent=4)

        url = "https://quickbooks.api.intuit.com/v3/company/%s/%s" % \
              (self.company_id, qbbo.lower())

        if self.verbosity > 0:
            if qbbo in ["Employee", "Vendor"]:
                reffer = "called %s" % json_dict.get("DisplayName")
            elif qbbo in ["Account", "Class", "Customer", "Item",]:
                reffer = "called %s" % json_dict.get("FullyQualifiedName")
            elif qbbo in ["Attachable"]:
                reffer = "called %s" % json_dict.get(
                    "FileName", "<no FileName>")
            else:
                reffer = "labeled %s" % json_dict.get(
                    "DocNumber", "<no DocNumber>")

            print "Deleting %s %s (%s)." % (qbbo, Id, reffer)

        response = self.hammer_it("POST", url, request_body, content_type,
                                  **{"params":{"operation":"delete"}})

        if object_id in getattr(self, attr_name):
            # Even if it failed, best to delete it from the cache...
            del(getattr(self, attr_name)[object_id])
        
        if not qbbo in response:
            if self.verbosity > 0:
                print "It looks like the delete failed for {} {}.".format(
                    qbbo, object_id)

            return response
                          
        return response[qbbo]

    def upload_file(self, path, name="same", upload_type="automatic",
                    qbbo=None, Id=None):
        """
        Uploads a file that can be linked to a specific transaction (or other
         entity probably), or not...

        Either way, it should return the id the attachment.
        """

        url = "https://quickbooks.api.intuit.com/v3/company/%s/upload" % \
              self.company_id

        bare_name, extension = path.rsplit("/",1)[-1].rsplit(".",1)

        if upload_type == "automatic":
            upload_type = "application/%s" % extension

        if name == "same":
            name = "{}.{}".format(bare_name, extension)

        result = self.hammer_it("POST", url, None,
                                "multipart/form-data",
                                file_name=path)

        aid = attachment_id = result[
            "AttachableResponse"][0]["Attachable"]["Id"]

        # Because the case of the file name is not preserved (because of the
        #  implementation of this particular API), we have to update the
        #  object's name
        att = self.read_object("Attachable", aid) # to cache it too...
        att["FileName"] = name        

        if qbbo and Id:
            # This file should not be attached to any other object since
            #  we're just uploading it, so we should be creating a NEW
            #  EntityRef dict here, not adding to an existing one...
            if self.vb > 3:
                print "Attaching {} to {}/{}!".format(aid, qbbo, Id)
            att_blob = att.get("AttachableRef", [])
            att_blob.append({"EntityRef" : {"value" : Id, "type"  : qbbo,}})
            att["AttachableRef"] = att_blob

        att_update_result = self.update_object("Attachable", aid, att)

        return attachment_id

    def download_file(self, attachment_id, path, only_if_newly_updated=True):
        """
        Download a file to the requested (or default) directory, then also
         return a download link for convenience.

        Only download the file if it a) does not already exist OR b) if the
         update timestamp on the file (as reported by the OS) is EARLIER
         than the updatestamp of the Attachable object
        """

        if os.path.exists(path):
            file_mtime = pandas.to_datetime(os.path.getmtime(path)*1000000000)
            atch_mtime = pandas.to_datetime(
                self.get_objects("Attachable")[attachment_id][
                    "MetaData"]["LastUpdatedTime"])

            if file_mtime >= atch_mtime:
                if self.vb > 3:
                    print "Not redownloading attachment, which is newer than"
                    print " the LastUpdatedTime of Attachable {}.".format(
                        attachment_id)
                    print "  file_mtime: {}".format(file_mtime)
                    print "  atch_mtime: {}".format(atch_mtime)
                    print "The newer file is called: {}".format(
                        path.rsplit("/", 1)[1])
                return "DOWNLOAD NOT REPEATED"
            
        url = "https://quickbooks.api.intuit.com/v3/company/%s/download/%s" % \
              (self.company_id, attachment_id)

        # Custom accept for file link!
        link =  self.hammer_it("GET", url, None, "json", accept="filelink")
        
        # No session required for file download
        success = False
        tries_remaining = 6

        # special hammer it routine for this very un-oauthed GET...
        while not success and tries_remaining >= 0:
            if self.verbosity > 1 and tries_remaining < 6:
                print "This is attempt #%d to download Attachable id %s." % \
                    (6-tries_remaining+1, attachment_id)

            try:
                my_r = requests.get(link)

                with open(path, 'wb') as f:
                    for chunk in my_r.iter_content(1024):
                        f.write(chunk)

                success = True

            except:
                tries_remaining -= 1
                time.sleep(1)
                
                if tries_remaining == 0:
                    print "Max retries reached...download failed!"
                    raise
                                   
        return link

    def capture_changes(self, since, qbbo_list="all"):
        """
        https://developer.intuit.com/docs/api/accounting/ChangeDataCapture

        THIS ONLY GETS YOU THINGS AS RECENT AS THE LAST 30 DAYS!!!

        YOU MUST PASS A UTC TIME TO THIS METHOD UNLESS YOU PASS A STRING
         WITH THE CORRECTLY (INTUIT)-FORMATTED OFFSET BUILT IN...
        """
        url = "https://qb.sbfinance.intuit.com/v3/company/{}/cdc".format(
            self.company_id)
        
        if qbbo_list == "all":
            qbbo_list = self._BUSINESS_OBJECTS

        if not since:
            # get the max available by default
            since = datetime.datetime.utcnow().replace(
                tzinfo=pytz.utc) - datetime.timedelta(days=29)
            
        if isinstance(since, datetime.datetime):            
            # WE'RE ASSUMING UTC TIME HERE!!!
            since = since.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

        test_time = datetime.datetime.strptime(
            str(since), "%Y-%m-%dT%H:%M:%S.000-00:00")
        test_days = (datetime.datetime.now() - test_time).days

        if test_days > 29:
            print "You asked for changes since {}".format(test_time)
            print "That's {} days ago!".format(test_days)
            
            raise Exception("You can only get up to 30 days of changes.")

        resp = self.hammer_it(
            "GET", url, "", "", **{
                "params" : {
                    "changedSince" : since,
                    "entities"     : ",".join(qbbo_list)}})

        # This will be a list of dictionaries, each of which relates to
        #  a specific response...
        return resp
        
        
    def hammer_it(self, request_type, url, request_body, content_type,
                  accept = 'json', file_name=None, **req_kwargs):
        """
        A slim version of simonv3's excellent keep_trying method. Among other
         trimmings, it assumes we can only use v3 of the
         QBO API. It also allows for requests and responses
         in xml OR json. (No xml parsing added yet but the way is paved...)
        """
        if not self.session:
            self.session = self._get_session()

        session = self.session
            
        #haven't found an example of when this wouldn't be True, but leaving
        #it for the meantime...
        header_auth = True

        if accept == "filelink":
            headers = {}

        else:
            headers = {'Accept': 'application/%s' % accept}

        if file_name == None:
            if not request_type == "GET":
                headers.update({'Content-Type': 
                                'application/%s' % content_type})

        else:
            # Avoid full paths in filenames...
            fn = file_name + ""
            if "/" in fn:
                fn = fn.rsplit("/", 1)[1]
                
            # Special request construction in the case of a file upload
            boundary = "-------------PythonMultipartPost"
            headers.update({ 
                'Content-Type':
                #'application/json',
                'multipart/form-data; boundary=%s' % boundary,
                'Accept-Encoding':
                #'multipart/form-data; boundary=%s' % boundary,
                'gzip;q=1.0,deflate;q=0.6,identity;q=0.3',
                #'application/json',
                'User-Agent': 'OAuth gem v0.4.7',
                #'User-Agent': 'python2.7',
                #'Accept': '*/*',
                'Accept':'application/json',
                'Connection': 'close'
            })

            with open(file_name, "rb") as file_handler:
                binary_data = file_handler.read()

            extension = file_name.rsplit(".", 1)[1]

            mime_type = {
                "pdf" : "pdf",
                # because here: https://technet.microsoft.com/en-us/library/
                #  ee309278(office.12).aspx
                #"xlsx":"vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                # But then, on subsequent testing with a successful upload...
                "xlsx" : "vnd.ms-excel",
                "pptx" : "vnd.ms-powerpoint"}.get(extension, "plain/text")
            
            request_body = textwrap.dedent(
                """
                --%s
                Content-Disposition: form-data; name="file_content_0"; filename="%s"
                Content-Type: application/%s
                Content-Length: %d
                Content-Transfer-Encoding: binary

                %s

                --%s--
                """
            ) % (boundary, fn,
                 #content_type,
                 mime_type,
                 len(binary_data),
                 binary_data, boundary)
        
        trying       = True
        print_errors = False

        tries = 0

        # collect them to help troubleshoot later
        fault_list = []
        
        while trying:
            tries += 1
            if tries > 10:
                print "qbo.hammer_it() is giving up after 10 tries!"
                return None
            elif tries > 1:
                #we don't want to get shut out...
                time.sleep(1)

                if self.verbosity > 2:
                    print "(this is try #%d)" % tries

            try:
                if self.verbosity > 20:
                    print "headers:", headers
                    if not file_name:
                        print "request_body:", request_body
                    print "req_kwargs:", req_kwargs
                                            
                my_r = session.request(request_type, url, header_auth,
                                       self.company_id, headers=headers,
                                       data=request_body, verify=True,
                                       **req_kwargs)
           
                resp_cont_type = my_r.headers['content-type']
                
            except:
                if self.verbosity > 5:
                    import traceback;traceback.print_exc()
                    if self.verbosity > 15:
                        import ipbd;ipbd.set_trace()
                fault_list.append("<Failed to get response>")
                if tries < 10:
                    continue
                else:
                    print "qbo.hammer_it() failed to get a response"
                    print "after {} tries:".format(tries)
                    for fault in fault_list:
                        print fault

            if 'xml' in resp_cont_type:
                result = ET.fromstring(my_r.content)
                rough_string = ET.tostring(result, "utf-8")
                reparsed = minidom.parseString(rough_string)
                '''
                if self.verbosity > 7:
                    print reparsed.toprettyxml(indent="\t")
                '''
                if self.verbosity > 3:
                    print my_r, my_r.reason,

                    if my_r.status_code in [503]:
                        print " (Service Unavailable)"

                    elif my_r.status_code in [401]:
                        print " (Unauthorized -- a dubious response)"

                    else:
                        print " (xml parse failed)"

                if self.verbosity > 8:
                    print my_r.text
                    result = None

            elif 'json' in resp_cont_type:
                try:
                    result = my_r.json()

                except:
                    result = {"Fault" : {"type":"(synthetic, inconclusive)"}}

                if "Fault" in result:
                    if self.verbosity > 3:
                        print my_r, my_r.reason, my_r.text
                        
                    fault_list.append(result)
                    
                    if "type" in result["Fault"] and \
                       result["Fault"]["type"] == "ValidationFault":
                        # Don't try 10 times; this won't get any better
                        trying = False
                        print_errors = True

                    elif tries >= 10:
                        trying = False
                        print_errors = True

                else:
                    #sounds like a success
                    trying = False

                if (not trying and print_errors):
                    print "Giving up after {} tries. The fault list:".format(
                        tries)
                    
                    for fault in fault_list:
                        print json.dumps(fault, indent=1)

            elif 'plain/text' in resp_cont_type or accept == 'filelink':
                if not "Fault" in my_r.text:
                    trying = False

                else:
                    fault_list.append(my_r.text)
                                        
                    if tries >= 10:
                        trying = False
                        print "Failed to get file link after {} tries.".format(
                            tries)
                        print "The faults:"
                        for fault in fault_list:
                            print fault
                    
                result = my_r.text

            elif 'text/html' in resp_cont_type:
                if self.verbosity > 0:
                    print "Hmmmm....why is text/html the resp_cont_type?"
                    import ipdb;ipdb.set_trace()
                else:
                    raise Exception("WTF?")

            else:
                raise NotImplementedError("How do I parse a %s response?" \
                                          #% accept)
                                          % resp_cont_type)

        return result

    def get_report(self, report_name, params = None):
        """
        Tries to use the QBO reporting API:
        https://developer.intuit.com/docs/0025_quickbooksapi/
         0050_data_services/reports
        """

        if params == None:
            params = {}

        url = "https://quickbooks.api.intuit.com/v3/company/%s/" % \
              self.company_id + "reports/%s" % report_name

        return self.hammer_it("GET", url, None, "json", **{"params" : params})
    
    def query_objects(self, business_object, params={}, query_tail="",
                      count_only=False):
        """
        Runs a query-type request against the QBOv3 API
        Gives you the option to create an AND-joined query by parameter
            or just pass in a whole query tail
        The parameter dicts should be keyed by parameter name and
            have twp-item tuples for values, which are operator and criterion

        count_only allows you to figure out how many objects there are
         without actually pulling all of them. This is VERY important if you
         want to figure out if something (created in the past) has been deleted.
        """

        if business_object not in self._BUSINESS_OBJECTS:
            if business_object in self._biz_object_correctors:
                business_object = self._biz_object_correctors[business_object]

            else:
                raise Exception("%s not in list of QBO Business Objects." %  \
                                business_object + " Please use one of the " + \
                                "following: %s" % self._BUSINESS_OBJECTS)

        #eventually, we should be able to select more than just *,
        #but chances are any further filtering is easier done with Python
        #than in the query...

        if count_only:
            query_string="SELECT COUNT(*) FROM %s" % business_object
        else:
            query_string="SELECT * FROM %s" % business_object

        if query_tail == "" and not params == {}:
            #It's not entirely obvious what are valid properties for
            #filtering, so we'll collect the working ones here and
            #validate the properties before sending it
            #datatypes are defined here:
            #https://developer.intuit.com/docs/0025_quickbooksapi/
            #    0050_data_services/020_key_concepts/0700_other_topics

            props = {
                "TxnDate":"Date",
                "MetaData.CreateTime":"DateTime",      #takes a Date though
                "MetaData.LastUpdatedTime":"DateTime"  #ditto
            }

            p = params.keys()

            #only validating the property name for now, not the DataType
            if p[0] not in props:
                raise Exception("Unfamiliar property: %s" % p[0])

            query_string+=" WHERE %s %s %s" % (p[0],
                                               params[p[0]][0],
                                               params[p[0]][1])

            if len(p)>1:
                for i in range(1,len(p)+1):
                    if p[i] not in props:
                        raise Exception("Unfamiliar property: %s" % p[i])

                    query_string+=" AND %s %s %s" % (p[i],
                                                     params[p[i]][0],
                                                     params[p[i]][1])

        elif not query_tail == "":
            if not query_tail[0]==" ":
                query_tail = " "+query_tail
            query_string+=query_tail

        url = self.base_url_v3 + "/company/%s/query" % self.company_id

        
        results = self.query_fetch_more(
            r_type="POST", header_auth=True, realm=self.company_id,
            qb_object=business_object, original_payload=query_string)

        if count_only:
            if self.verbosity > 4:
                print "QBO counts {} {} objects".format(
                    results, business_object)
            return results
        
        if self.verbosity > 4:
            print "qbo.query_objects() Found %s %ss!" % (
                len(results), business_object)

        return results

    def get_objects(self, qbbo, requery=False, params = {}, query_tail = ""):
        """
        Rather than have to look up the account that's associate with an
        invoice item, for example, which requires another query, it might
        be easier to just have a local dict for reference.

        The same is true with linked transactions, so transactions can
        also be cloned with this method
        """

        #we'll call the attributes by the Business Object's name + 's',
        #case-sensitive to what Intuit's documentation uses

        if qbbo not in self._BUSINESS_OBJECTS:
            if qbbo in self._biz_object_correctors:
                qbbo = self._biz_object_correctors[qbbo]

            else:
                raise Exception("%s is not a valid QBO Business Object." % qbbo)

        elif qbbo in self._NAME_LIST_OBJECTS and query_tail == "":
            #to avoid confusion from 'deleted' accounts later...
            query_tail = "WHERE Active IN (true,false)"

        attr_name = qbbo+"s"

        #if we've already populated this list, only redo if told to
        #because, say, we've created another Account or Item or something
        #during the session

        if not hasattr(self, attr_name):
            setattr(self, attr_name, collections.OrderedDict())
            requery=True
        
        if requery:
            if self.verbosity > 3:
                print "Caching list of %ss." % qbbo
                if not params == {}:
                    print "params:\n%s" % params
                if query_tail:
                    print "query_tail:\n%s" % query_tail

            object_list = self.query_objects(qbbo, params, query_tail)

            if self.verbosity > 3:
                print "Found %s %ss!" % (len(object_list), qbbo)
            
            # Any previously stored objects (with the same ID) will
            #  be overwritten (which presumably is desirable)
            for obj in object_list:
                Id = obj["Id"]
                self.latest = max(
                    self.latest, obj["MetaData"]["LastUpdatedTime"])
                getattr(self, attr_name)[Id] = obj

        return getattr(self,attr_name)

    def object_dicts(self, qbbo_list = [], requery=False, params={},
                     query_tail=""):
        """
        returns a dict of dicts of ALL the Business Objects of
        each of these types (filtering with params and query_tail)
        """
        object_dicts = {}       #{qbbo:[object_list]}

        for qbbo in qbbo_list:
            if qbbo == "TimeActivity":
                #for whatever reason, this failed with some basic criteria, so
                query_tail = ""
            elif qbbo in self._NAME_LIST_OBJECTS and query_tail == "":
                #just something to avoid confusion from 'deleted' accounts later
                query_tail = "WHERE Active IN (true,false)"

            object_dicts[qbbo] = self.get_objects(
                qbbo, requery, params, query_tail)

        return object_dicts

    def names(self, requery=False, params = {},
              query_tail = "WHERE Active IN (true,false)"):
        """
        get a dict of every Name List Business Object (of every type)

        results are subject to the filter if applicable

        returned dict has two dimensions:
        name = names[qbbo][Id]
        """

        return self.object_dicts(self._NAME_LIST_OBJECTS, requery,
                                 params, query_tail)

    def transactions(self, requery=False, params = {}, query_tail = ""):
        """
        get a dict of every Transaction Business Object (of every type)

        results are subject to the filter if applicable

        returned dict has two dimensions:
        transaction = transactions[qbbo][Id]
        """
        return self.object_dicts(self._TRANSACTION_OBJECTS, requery,
                                        params, query_tail)

