"""Tests for XML-to-dict and dict-to-XML conversion.

Tests cover:
- xml_to_dict: basic elements, namespaces, force_list, empty elements,
  attributes, nested structures, real S3 response formats
- dict_to_xml: basic dicts, lists as repeated siblings, None as empty
  elements, roundtrip consistency, error cases
"""

import xml.etree.ElementTree as ET

import pytest

from api_parity.xml_body import dict_to_xml, xml_to_dict


# =============================================================================
# xml_to_dict tests
# =============================================================================


class TestXmlToDictBasic:
    """Basic XML-to-dict conversion."""

    def test_simple_elements(self) -> None:
        xml = b"<Root><Name>hello</Name><Count>42</Count></Root>"
        result = xml_to_dict(xml)
        assert result == {"Root": {"Name": "hello", "Count": "42"}}

    def test_nested_elements(self) -> None:
        xml = b"<Root><Parent><Child>value</Child></Parent></Root>"
        result = xml_to_dict(xml)
        assert result == {"Root": {"Parent": {"Child": "value"}}}

    def test_text_only_leaf(self) -> None:
        """Leaf element with only text content returns a plain string."""
        xml = b"<Root><Name>hello</Name></Root>"
        result = xml_to_dict(xml)
        assert result["Root"]["Name"] == "hello"
        assert isinstance(result["Root"]["Name"], str)

    def test_empty_element_becomes_none(self) -> None:
        """Self-closing element like <Prefix/> becomes None."""
        xml = b"<Root><Prefix/></Root>"
        result = xml_to_dict(xml)
        assert result == {"Root": {"Prefix": None}}

    def test_empty_element_with_whitespace(self) -> None:
        """Element with only whitespace text is treated as empty."""
        xml = b"<Root><Prefix>   </Prefix></Root>"
        result = xml_to_dict(xml)
        assert result == {"Root": {"Prefix": None}}

    def test_multiple_siblings_become_list(self) -> None:
        """Multiple sibling elements with the same tag become a list."""
        xml = b"<Root><Item>a</Item><Item>b</Item><Item>c</Item></Root>"
        result = xml_to_dict(xml)
        assert result == {"Root": {"Item": ["a", "b", "c"]}}

    def test_single_sibling_becomes_scalar(self) -> None:
        """A single child element is a scalar value, not a one-element list."""
        xml = b"<Root><Item>only</Item></Root>"
        result = xml_to_dict(xml)
        assert result == {"Root": {"Item": "only"}}

    def test_mixed_children_different_tags(self) -> None:
        """Children with different tags are separate keys."""
        xml = b"<Root><A>1</A><B>2</B><A>3</A></Root>"
        result = xml_to_dict(xml)
        assert result == {"Root": {"A": ["1", "3"], "B": "2"}}


class TestXmlToDictNamespaces:
    """Namespace stripping in XML-to-dict conversion."""

    def test_default_namespace_stripped(self) -> None:
        xml = b'<Root xmlns="http://example.com/ns"><Name>val</Name></Root>'
        result = xml_to_dict(xml)
        assert "Root" in result
        assert "Name" in result["Root"]
        assert result["Root"]["Name"] == "val"

    def test_s3_namespace_stripped(self) -> None:
        xml = (
            b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            b"<Name>my-bucket</Name>"
            b"</ListBucketResult>"
        )
        result = xml_to_dict(xml)
        assert result == {"ListBucketResult": {"Name": "my-bucket"}}

    def test_prefixed_namespace_stripped(self) -> None:
        xml = (
            b'<ns:Root xmlns:ns="http://example.com/ns">'
            b"<ns:Name>val</ns:Name>"
            b"</ns:Root>"
        )
        result = xml_to_dict(xml)
        assert "Root" in result
        assert result["Root"]["Name"] == "val"

    def test_xmlns_attribute_not_in_output(self) -> None:
        """The xmlns declaration itself should not appear as an @-prefixed key."""
        xml = b'<Root xmlns="http://example.com/ns"><Name>val</Name></Root>'
        result = xml_to_dict(xml)
        root_val = result["Root"]
        assert not any(k.startswith("@xmlns") for k in root_val if isinstance(root_val, dict))


class TestXmlToDictForceList:
    """force_list parameter for consistent list wrapping."""

    def test_single_element_forced_to_list(self) -> None:
        """When tag is in force_list, even a single child becomes a list."""
        xml = b"<Root><Item>only</Item></Root>"
        result = xml_to_dict(xml, force_list={"Item"})
        assert result == {"Root": {"Item": ["only"]}}

    def test_multiple_elements_still_list(self) -> None:
        """force_list doesn't change behavior for multiple siblings."""
        xml = b"<Root><Item>a</Item><Item>b</Item></Root>"
        result = xml_to_dict(xml, force_list={"Item"})
        assert result == {"Root": {"Item": ["a", "b"]}}

    def test_force_list_only_affects_specified_tags(self) -> None:
        """Tags not in force_list follow normal heuristic."""
        xml = b"<Root><Item>a</Item><Other>b</Other></Root>"
        result = xml_to_dict(xml, force_list={"Item"})
        assert result["Root"]["Item"] == ["a"]  # Forced to list
        assert result["Root"]["Other"] == "b"  # Scalar

    def test_force_list_with_nested_elements(self) -> None:
        xml = (
            b"<Root>"
            b"<Contents><Key>file.txt</Key><Size>100</Size></Contents>"
            b"</Root>"
        )
        result = xml_to_dict(xml, force_list={"Contents"})
        assert result == {
            "Root": {
                "Contents": [{"Key": "file.txt", "Size": "100"}],
            }
        }

    def test_empty_force_list(self) -> None:
        """Empty force_list is the same as no force_list."""
        xml = b"<Root><Item>a</Item></Root>"
        assert xml_to_dict(xml, force_list=set()) == xml_to_dict(xml)


class TestXmlToDictAttributes:
    """XML attribute handling."""

    def test_attributes_as_at_keys(self) -> None:
        xml = b'<Root><Tag id="123">content</Tag></Root>'
        result = xml_to_dict(xml)
        assert result == {"Root": {"Tag": {"@id": "123", "#text": "content"}}}

    def test_attribute_only_element(self) -> None:
        xml = b'<Root><Tag id="123"/></Root>'
        result = xml_to_dict(xml)
        assert result == {"Root": {"Tag": {"@id": "123"}}}

    def test_attribute_with_children(self) -> None:
        xml = b'<Root><Parent type="group"><Child>val</Child></Parent></Root>'
        result = xml_to_dict(xml)
        assert result == {
            "Root": {"Parent": {"@type": "group", "Child": "val"}}
        }

    def test_s3_acl_xsi_type(self) -> None:
        """S3 ACL uses xsi:type on Grantee. ElementTree expands the namespace
        prefix, so xsi:type becomes {http://...}type which is filtered out
        alongside other namespace-qualified attributes. For parity testing
        this is acceptable — both targets lose the attribute equally."""
        xml = (
            b'<Grant>'
            b'<Grantee xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            b' xsi:type="CanonicalUser">'
            b'<ID>abc123</ID>'
            b'</Grantee>'
            b'<Permission>FULL_CONTROL</Permission>'
            b'</Grant>'
        )
        result = xml_to_dict(xml)
        grantee = result["Grant"]["Grantee"]
        assert grantee["ID"] == "abc123"
        # xsi:type is NOT preserved — ElementTree expands it to a
        # {uri}-prefixed attribute name, which the namespace filter drops.
        assert "@xsi:type" not in grantee


class TestXmlToDictS3Responses:
    """Real S3 response formats."""

    def test_list_objects_v2(self) -> None:
        xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            b"<Name>my-bucket</Name>"
            b"<Prefix/>"
            b"<KeyCount>2</KeyCount>"
            b"<MaxKeys>1000</MaxKeys>"
            b"<IsTruncated>false</IsTruncated>"
            b"<Contents>"
            b"<Key>file1.txt</Key>"
            b"<Size>1024</Size>"
            b"<StorageClass>STANDARD</StorageClass>"
            b"</Contents>"
            b"<Contents>"
            b"<Key>file2.txt</Key>"
            b"<Size>2048</Size>"
            b"<StorageClass>STANDARD</StorageClass>"
            b"</Contents>"
            b"</ListBucketResult>"
        )
        result = xml_to_dict(xml, force_list={"Contents"})
        root = result["ListBucketResult"]

        assert root["Name"] == "my-bucket"
        assert root["Prefix"] is None
        assert root["KeyCount"] == "2"
        assert root["IsTruncated"] == "false"
        assert len(root["Contents"]) == 2
        assert root["Contents"][0]["Key"] == "file1.txt"
        assert root["Contents"][1]["Key"] == "file2.txt"

    def test_list_buckets(self) -> None:
        xml = (
            b'<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            b"<Buckets>"
            b"<Bucket><Name>bucket-a</Name></Bucket>"
            b"<Bucket><Name>bucket-b</Name></Bucket>"
            b"</Buckets>"
            b"<Owner><ID>owner-id</ID></Owner>"
            b"</ListAllMyBucketsResult>"
        )
        result = xml_to_dict(xml, force_list={"Bucket"})
        root = result["ListAllMyBucketsResult"]

        assert len(root["Buckets"]["Bucket"]) == 2
        assert root["Buckets"]["Bucket"][0]["Name"] == "bucket-a"
        assert root["Owner"]["ID"] == "owner-id"

    def test_error_response(self) -> None:
        xml = (
            b"<Error>"
            b"<Code>NoSuchKey</Code>"
            b"<Message>The resource you requested does not exist</Message>"
            b"<Resource>/mybucket/myfoto.jpg</Resource>"
            b"<RequestId>4442587FB7D0A2F9</RequestId>"
            b"</Error>"
        )
        result = xml_to_dict(xml)
        assert result == {
            "Error": {
                "Code": "NoSuchKey",
                "Message": "The resource you requested does not exist",
                "Resource": "/mybucket/myfoto.jpg",
                "RequestId": "4442587FB7D0A2F9",
            }
        }

    def test_single_object_without_force_list(self) -> None:
        """Without force_list, a single Contents is a dict, not a list.
        This is the documented limitation — both targets get the same
        conversion, so parity comparison still works."""
        xml = (
            b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            b"<Name>bucket</Name>"
            b"<Contents><Key>only.txt</Key></Contents>"
            b"</ListBucketResult>"
        )
        result = xml_to_dict(xml)
        # Without force_list, single Contents is a dict
        assert isinstance(result["ListBucketResult"]["Contents"], dict)

        # With force_list, it becomes a one-element list
        result2 = xml_to_dict(xml, force_list={"Contents"})
        assert isinstance(result2["ListBucketResult"]["Contents"], list)
        assert len(result2["ListBucketResult"]["Contents"]) == 1


class TestXmlToDictErrors:
    """Error handling for malformed XML."""

    def test_malformed_xml_raises(self) -> None:
        with pytest.raises(ET.ParseError):
            xml_to_dict(b"<not valid xml")

    def test_empty_bytes_raises(self) -> None:
        with pytest.raises(ET.ParseError):
            xml_to_dict(b"")


# =============================================================================
# dict_to_xml tests
# =============================================================================


class TestDictToXmlBasic:
    """Basic dict-to-XML conversion."""

    def test_simple_dict(self) -> None:
        data = {"Root": {"Name": "hello", "Count": "42"}}
        result = dict_to_xml(data)
        # Parse back to verify structure
        root = ET.fromstring(result)
        assert root.tag == "Root"
        assert root.find("Name").text == "hello"
        assert root.find("Count").text == "42"

    def test_nested_dict(self) -> None:
        data = {"Root": {"Parent": {"Child": "value"}}}
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        assert root.find("Parent/Child").text == "value"

    def test_none_becomes_empty_element(self) -> None:
        data = {"Root": {"Prefix": None}}
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        prefix = root.find("Prefix")
        assert prefix is not None
        assert prefix.text is None
        assert len(prefix) == 0  # No children

    def test_list_becomes_repeated_siblings(self) -> None:
        data = {"Root": {"Item": ["a", "b", "c"]}}
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        items = root.findall("Item")
        assert len(items) == 3
        assert [item.text for item in items] == ["a", "b", "c"]

    def test_list_of_dicts(self) -> None:
        data = {
            "Root": {
                "Item": [
                    {"Key": "file1.txt", "Size": "100"},
                    {"Key": "file2.txt", "Size": "200"},
                ]
            }
        }
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        items = root.findall("Item")
        assert len(items) == 2
        assert items[0].find("Key").text == "file1.txt"
        assert items[1].find("Size").text == "200"

    def test_numeric_values_become_text(self) -> None:
        data = {"Root": {"Count": 42, "Price": 9.99, "Active": True}}
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        assert root.find("Count").text == "42"
        assert root.find("Price").text == "9.99"
        assert root.find("Active").text == "True"

    def test_returns_bytes_with_xml_declaration(self) -> None:
        data = {"Root": {"Name": "test"}}
        result = dict_to_xml(data)
        assert isinstance(result, bytes)
        assert result.startswith(b"<?xml")

    def test_text_key_becomes_element_text(self) -> None:
        """#text key in a dict becomes the element's text content."""
        data = {"Root": {"Tag": {"@id": "123", "#text": "content"}}}
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        tag = root.find("Tag")
        assert tag.text == "content"


class TestDictToXmlErrors:
    """Error handling for invalid input."""

    def test_multiple_top_level_keys_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one top-level key"):
            dict_to_xml({"A": "1", "B": "2"})

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one top-level key"):
            dict_to_xml({})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one top-level key"):
            dict_to_xml("not a dict")  # type: ignore[arg-type]


class TestDictToXmlS3Requests:
    """S3-style request body serialization."""

    def test_delete_objects(self) -> None:
        """S3 DeleteObjects request body."""
        data = {
            "Delete": {
                "Object": [
                    {"Key": "file1.txt"},
                    {"Key": "file2.txt"},
                ],
                "Quiet": "true",
            }
        }
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        assert root.tag == "Delete"
        objects = root.findall("Object")
        assert len(objects) == 2
        assert objects[0].find("Key").text == "file1.txt"
        assert root.find("Quiet").text == "true"

    def test_complete_multipart_upload(self) -> None:
        """S3 CompleteMultipartUpload request body."""
        data = {
            "CompleteMultipartUpload": {
                "Part": [
                    {"PartNumber": "1", "ETag": '"etag1"'},
                    {"PartNumber": "2", "ETag": '"etag2"'},
                ]
            }
        }
        result = dict_to_xml(data)
        root = ET.fromstring(result)
        parts = root.findall("Part")
        assert len(parts) == 2
        assert parts[0].find("PartNumber").text == "1"
        assert parts[1].find("ETag").text == '"etag2"'


# =============================================================================
# Roundtrip tests
# =============================================================================


class TestRoundtrip:
    """Verify that xml_to_dict → dict_to_xml → xml_to_dict produces
    consistent results for data-oriented XML (no attributes, no mixed content)."""

    def test_simple_roundtrip(self) -> None:
        original = {"Root": {"Name": "hello", "Count": "42"}}
        xml_bytes = dict_to_xml(original)
        back = xml_to_dict(xml_bytes)
        assert back == original

    def test_list_roundtrip(self) -> None:
        """Lists survive roundtrip because dict_to_xml creates repeated
        siblings, and xml_to_dict converts multiple siblings to a list."""
        original = {"Root": {"Item": ["a", "b", "c"]}}
        xml_bytes = dict_to_xml(original)
        back = xml_to_dict(xml_bytes)
        assert back == original

    def test_nested_roundtrip(self) -> None:
        original = {
            "Delete": {
                "Object": [
                    {"Key": "file1.txt"},
                    {"Key": "file2.txt"},
                ],
            }
        }
        xml_bytes = dict_to_xml(original)
        back = xml_to_dict(xml_bytes)
        assert back == original

    def test_none_roundtrip(self) -> None:
        original = {"Root": {"Prefix": None, "Name": "test"}}
        xml_bytes = dict_to_xml(original)
        back = xml_to_dict(xml_bytes)
        assert back == original

    def test_single_element_list_loses_list_without_force_list(self) -> None:
        """A single-element list becomes a scalar after roundtrip unless
        force_list is used. This is the documented limitation."""
        original = {"Root": {"Item": ["only"]}}
        xml_bytes = dict_to_xml(original)
        # Without force_list, the single Item becomes a scalar
        back = xml_to_dict(xml_bytes)
        assert back == {"Root": {"Item": "only"}}
        # With force_list, it stays a list
        back_forced = xml_to_dict(xml_bytes, force_list={"Item"})
        assert back_forced == original
