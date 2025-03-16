import copy
import re
import cssutils
from collections import deque
from lxml.html import etree
from io import StringIO

from .utils import ElementNode, TagNameList, MapTagNameList, stringfy_selector
from .active_elements import ActiveElements
import logging
cssutils.log.setLevel(logging.CRITICAL)

class HTMLTree:
    def __init__(self):
        self.elementNodes = [ElementNode] * 100000
        self.rawNode2id: dict = {}
        self.element2id: dict = {}
        self.id2rawNode: dict = {}
        self.valid: list[bool] = [False] * 100000
        self.nodeCounts: int
        self.nodeDict = [0] * 100000
        self.element_value = {}
        self.invisible_elements=[]
    
    def fetch_html_content(self, html_content) -> str:
        """
        Fetch the html content to extract and prune the DOM tree based on visibility styles.
        """
        self.__init__()
        parser = etree.HTMLParser(remove_comments=True)
        self.tree = etree.parse(StringIO(html_content), parser)
        self.copy_tree = copy.deepcopy(self.tree)
        root = self.tree.getroot()
        self.init_html_tree(root)
        self.build_html_tree(root)
        return self.prune_tree()

    @staticmethod
    def build_node(node, idx: int) -> ElementNode:
        elementNode = ElementNode()
        elementNode["nodeId"] = idx
        elementNode["tagName"] = node.tag
        elementNode["text"] = node.text if node.text else ""
        elementNode["attributes"] = node.attrib
        elementNode["childIds"] = []
        elementNode["parentId"] = ""
        elementNode["siblingId"] = ""
        elementNode["twinId"] = ""
        elementNode["depth"] = 1
        elementNode["htmlContents"] = etree.tostring(
            node, pretty_print=True).decode()
        return elementNode

    def build_mapping(self) -> None:
        self.element2id = {value["nodeId"]: index for index,
                           value in enumerate(self.elementNodes)}
        self.id2rawNode = {str(index): value for value,
                           index in self.rawNode2id.items()}

    def init_html_tree(self, root) -> None:
        node_queue = deque([root])
        node_id = 0
        while node_queue:
            node = node_queue.popleft()
            self.elementNodes[node_id] = self.build_node(node, node_id)
            self.rawNode2id[node] = node_id
            node_id += 1
            for child in node.getchildren():
                node_queue.append(child)
        self.build_mapping()
        self.nodeCounts = node_id
        self.valid = self.valid[:self.nodeCounts + 1]

    def build_html_tree(self, root) -> None:
        node_queue = deque([root])
        root_id = self.rawNode2id[root]
        self.elementNodes[root_id]["parentId"] = -1
        while node_queue:
            node = node_queue.popleft()
            parent_id = self.rawNode2id[node]
            tag_st = {}
            sibling_id = 1
            for child in node.getchildren():
                child_id = self.rawNode2id[child]
                tag_name = self.elementNodes[child_id].get("tagName")
                tag_st[tag_name] = tag_st.get(tag_name, 0) + 1
                twin_id = tag_st.get(tag_name)
                self.elementNodes[parent_id]["childIds"].append(child_id)
                self.elementNodes[child_id]["parentId"] = parent_id
                self.elementNodes[child_id]["twinId"] = twin_id
                self.elementNodes[child_id]["depth"] = self.elementNodes[parent_id]["depth"] + 1
                self.elementNodes[child_id]["siblingId"] = sibling_id
                node_queue.append(child)
                sibling_id += 1
        self.pruningTreeNode = copy.deepcopy(self.elementNodes)

    def _get_xpath(self, idx: int) -> str:
        xpath = self.elementNodes[idx].get("xpath")
        return xpath
    
    def _get_selector(self, idx: int):
        selector = self.elementNodes[idx].get("selector")
        return selector

    def is_valid(self, idx: int) -> bool:
        node = self.pruningTreeNode[idx]
        if node["tagName"] in TagNameList:
            return ActiveElements.is_valid_element(node)
        return False

    def prune_tree(self) -> str:
        """Traverse each element to determine if it is valid and prune"""
        result_list = []
        root = self.pruningTreeNode[0]
        if root is None:
            result_list = []
        stack = [root]
        while stack:
            node = stack.pop()
            nodeId = node["nodeId"]
            result_list.append(nodeId)
            children = []
            for childId in node["childIds"]:
                childNode = self.pruningTreeNode[childId]
                children.append(childNode)
            stack.extend(children)
        result = result_list[::-1]
        for nodeId in result:
            if self.is_valid(nodeId) or self.valid[nodeId] is True:
                rawNode = self.id2rawNode[str(nodeId)]
                html_contents = etree.tostring(
                    rawNode, pretty_print=True).decode()
                self.pruningTreeNode[nodeId]["htmlContents"] = html_contents
                self.valid[nodeId] = True
                current_id = nodeId
                while self.pruningTreeNode[current_id]["parentId"] != -1:
                    parent_id = self.pruningTreeNode[current_id]["parentId"]
                    self.valid[parent_id] = True
                    current_id = parent_id
            else:
                rawNode = self.id2rawNode[str(nodeId)]
                rawNode.getparent().remove(rawNode)
                current_node = self.pruningTreeNode[nodeId]
                current_node["htmlContents"] = ""
                parentid = current_node["parentId"]
                # self.pruningTreeNode[parentid]["childIds"].remove(nodeId)
                for child in self.pruningTreeNode[parentid]["childIds"]:
                    child_node=self.pruningTreeNode[child]
                    self.set_invalid(child_node)
                # self.valid[nodeId] = False
        return self.pruningTreeNode[0]["htmlContents"]

    def get_element_contents(self, idx: int) -> str:
        node = self.elementNodes[idx]
        html_content = node["htmlContents"]
        return html_content
    
    def resolve_element_semantics(self, element: ElementNode) -> (str, int, str):  # type: ignore
        """Resolves the semantic information of the element, including tag name, tag index and valid content"""
        tag_name = ActiveElements.get_element_tagName(element)
        tag_idx = element["nodeId"]
        validContent = self.process_element_contents(element)
        
        if tag_name == "span":
            # If the tag name is span, traverse the parent and grandparent nodes to merge the span content
            parent_id = element["parentId"]
            if parent_id != -1: 
                parent_node = self.pruningTreeNode[parent_id]
                parent_tag_name, parent_tag_idx, _ = self.resolve_element_semantics(parent_node)
                # Limit traversal to two levels
                for child_id in parent_node["childIds"]:
                    child_node = self.pruningTreeNode[child_id]
                    if child_node.get("tagName") == "span":
                        validContent =validContent + self.process_element_contents(child_node) + ' '
                        self.set_invalid(child_node)  # Mark child as invalid
                    # Traverse one more level
                    for grandchild_id in child_node["childIds"]:
                        grandchild_node = self.pruningTreeNode[grandchild_id]
                        if grandchild_node.get("tagName") == "span":
                            validContent = validContent + self.process_element_contents(grandchild_node) + ' '
                            self.set_invalid(grandchild_node)  # Mark grandchild as invalid
            return (parent_tag_name, tag_idx, validContent)

        if tag_name == "unknown":
            tag_name = element["tagName"]
            tag_idx = element["nodeId"]
            validContent = self.process_element_contents(element)
            if tag_name in MapTagNameList:
                # If tag name is in MapTagNameList, return the parent tag name
                parent_node = self.pruningTreeNode[element["parentId"]]
                parent_tag_name, parent_tag_idx, _ = self.resolve_element_semantics(parent_node)
                return (parent_tag_name, tag_idx, validContent)
            else:
                # If tag name is not in MapTagNameList, return statictext
                return ("statictext", tag_idx, validContent)
        return (tag_name, tag_idx, validContent)

    def set_invalid(self, node: ElementNode) -> None:
        """Set the node as invalid"""
        node_id = node["nodeId"]
        self.valid[node_id] = False
        node["htmlContents"] = "" 

    def set_invalid_children(self, node: ElementNode) -> None:
        """Set all the descendants of the node as invalid"""
        for child_id in node["childIds"]:
            child_node = self.pruningTreeNode[child_id]
            self.set_invalid_children(child_node) 

    def _build_dom_tree(self, eval_page: dict) -> str:
        """Parse the node from eval_page and build the DOM tree"""
        js_node_map = eval_page.get('map')
        js_root_id = eval_page.get('root')
        if js_node_map is None or js_root_id is None:
            return ""

        # Parse the node
        for id, node_data in js_node_map.items():
            node, child_ids = self._parse_node(node_data)
            if node is None:
                continue

            self.elementNodes[int(id)] = node
            self.valid[int(id)] = True

            # Add child nodes to the current node
            if node.get('type') == 'ELEMENT_NODE':
                node["childIds"].extend(filter(None, child_ids))

        # Add parent nodes to the child nodes
        for node in self.elementNodes:
            for child_id in node["childIds"]:
                if isinstance(child_id, int):
                    self.elementNodes[child_id]["parentId"] = node["nodeId"]

        self.nodeCounts = len(js_node_map)
        self.elementNodes = self.elementNodes[:self.nodeCounts]
        self.pruningTreeNode = copy.deepcopy(self.elementNodes)
        # logging.info(self.pruningTreeNode)

        # Start building the DOM tree
        stack = [(self.pruningTreeNode[js_root_id], 0)]
        num = 0
        contents = []
        effective_depths = {}
        last_content_depth = -1

        while stack:
            node, current_depth = stack.pop()
            if node is None or not self.valid[node["nodeId"]]:
                continue

            tag_name, tag_idx, validContent = self.resolve_element_semantics(node)
            content_text = validContent or self.process_element_contents(node)
            num += 1
            self.nodeDict[num] = tag_idx
            attributes_text = self._get_attributes_string(node)

            # If the node itself has no content, pass attributes to the first descendant with content
            if not re.search(r'[a-zA-Z0-9]', content_text) and attributes_text:
                descendant_stack = [node]
                while descendant_stack:
                    current_node = descendant_stack.pop()
                    for child_id in reversed(current_node["childIds"]):
                        child_node = self.pruningTreeNode[child_id]
                        child_content = self.process_element_contents(child_node)
                        if re.search(r'[a-zA-Z0-9]', child_content):
                            for attr in ["aria-expanded", "aria-haspopup", "focused", "selected"]:
                                if attr in node["attributes"]:
                                    child_node["attributes"][attr] = node["attributes"][attr]
                            descendant_stack = []  # Exit the loop
                            break
                        descendant_stack.append(child_node)

            if re.search(r'[a-zA-Z0-9]', content_text) and node.get("isVisible"):
                if current_depth not in effective_depths:
                    effective_depths[current_depth] = effective_depths.get(last_content_depth, -1) + 1
                    last_content_depth = current_depth
                effective_indent_level = effective_depths[current_depth]
                contents.append("  " * effective_indent_level + f"[{num}] {tag_name} '{content_text.strip()}' {attributes_text}\n")
                self.element_value[str(tag_idx)] = content_text

            # Adjust depth for children
            children = [self.pruningTreeNode[child_id] for child_id in node["childIds"]]
            for child in reversed(children):
                stack.append((child, current_depth + 1))

        return ''.join(contents)

    def _get_attributes_string(self, node: ElementNode) -> str:
        """Get the expanded, haspopup, focused, selected attributes of the node"""
        attributes = []
        for attr in ["aria-expanded", "aria-haspopup", "focused", "selected"]:
            value = node.get("attributes").get(attr)
            if value:
                attributes.append(f'{attr.split("-")[-1]}: {value}')
        return ' '.join(attributes)
    
    def _parse_node(self, node_data: dict) -> ElementNode:
        """Process the node from dict to ElementNode"""
        if not node_data:
            return None, []

        # Parse text nodes
        if node_data.get('type') == 'TEXT_NODE':
            text_node = ElementNode(
                nodeId = node_data.get('index', -1),
                type = "TEXT_NODE",
                childIds = [],
                parentId = None,
                tagName = node_data.get('tagName', ''),
                text = node_data.get('text', ''),
                attributes = node_data.get('attributes', {}),
                selector = node_data.get('selector', ''),
                xpath = node_data.get('xpath', ''),
                isVisible = node_data.get('isVisible', False),
			)
            return text_node, []
        
        # Parse element nodes
        element_node = ElementNode(
            nodeId = node_data.get('index', -1),
            type = "ELEMENT_NODE",
            childIds = [],
            parentId = None,
            tagName = node_data.get('tagName', ''),
            text = node_data.get('text', ''),
            attributes = node_data.get('attributes', {}),
            selector = node_data.get('selector', ''),
            xpath = node_data.get('xpath', ''),
            isVisible = node_data.get('isVisible', False),
        )

        # Combine pseudo-element text with node text
        pseudo_elements = node_data.get('pseudoElements', {})
        before_text = pseudo_elements.get('before', {}).get('content', '').strip('"')
        after_text = pseudo_elements.get('after', {}).get('content', '').strip('"')

        # Filter out -moz-alt-content
        if '-moz-alt-content' in before_text:
            before_text = before_text.replace('-moz-alt-content', '')
        if '-moz-alt-content' in after_text:
            after_text = after_text.replace('-moz-alt-content', '')

        combined_text = f"{before_text}{element_node['text']}{after_text}".strip()
        element_node['text'] = combined_text
    
        children_ids = node_data.get('children', [])
        return element_node, children_ids

    def get_selector_and_xpath(self, idx: int) -> (str, str):  # type: ignore
        try:
            selector = self._get_selector(idx)
            xpath = self._get_xpath(idx)
            return selector, xpath
        except:
            print(f"can't locate element")

    @staticmethod
    def process_element_contents(element: ElementNode) -> str:
        # TODO Add appropriate interactive element information, currently only processing interactive elements with text attributes   
        html_text = ActiveElements.get_element_value(element)
        if html_text is None:
            return ""
        return html_text.replace("\n", "").replace("\t", "").strip()

    def get_element_value(self, element_id: int) -> str:
        return self.element_value[str(element_id)]

__all__ = [
    "HTMLTree"
]